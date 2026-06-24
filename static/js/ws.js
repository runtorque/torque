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
  engineer_panel_split_fraction: 0.30,
  context_panel_split_ratio: 0.38,
  supervisor_panel_state: {},
  agent_message_history: {},
  direct_messages_by_agent: {},
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
var _resyncPending = false;
var _awaitingFullState = false;
var _pendingDeltaSurfaceInvalidations = null;
var _pendingDeltaSurfaceRenderFrame = 0;
// Track whether the user is actively interacting with the DOM in a way
// that a delta-driven rerender would interrupt. Two distinct interaction
// modes share one flag:
//
// (a) Pointer press: pointerdown..pointerup window. While pressing, defer
//     DOM-replacing renders so the press target survives long enough for
//     the browser to fire the synthetic click on mouseup. Without this,
//     a delta firehose (e.g. worker mcp_call_append from :224)
//     rerenders between pointerdown and pointerup, swapping the target
//     out and silently suppressing the click.
//
// (b) Text input typing / IME composition: keydown..keyup window inside
//     a text field, plus compositionstart..compositionend for IME. The
//     panel renderer captures `selectionStart`/`selectionEnd` BEFORE the
//     innerHTML rebuild and restores them AFTER, but the value captured
//     reflects state BEFORE the keystroke that's still in flight; if the
//     browser is mid-keystroke when the rebuild runs, the new node is
//     reattached but the in-flight char/composition is lost. Deferring
//     renders during the keystroke window avoids the race entirely.
//
// Critical: the post-interaction flush must NOT run inside the capture
// phase of the closing event (pointerup, keyup, compositionend), because
// replacing DOM there changes the event's target before the browser
// emits the synthetic follow-up (click for pointer, input for keystroke)
// — same suppression by a different path. We schedule the flush in a
// microtask / rAF after the closing event so the browser delivers the
// follow-up on the original target first, then deferred renders apply.
var _userPressing = false;  // legacy name; covers pointer + keyboard interaction now
// TORQUE:264 follow-up: hover-defer for agent-card tooltips. Agent cards expose
// their full status / activity / branch text via a CSS `:hover::after`
// pseudo-element on `.agent-card-tooltip` (style.css:1142). When the user
// hovers an active card and the worker fires events at firehose rate, the
// surface invalidation pipeline blasts `main.innerHTML` and destroys the
// hovered card mid-`:hover`; the tooltip vanishes, the new card appears, the
// pointer re-enters, the tooltip reappears — visible flicker. Defer
// DOM-replacing renders while the user is hovering an agent-card tooltip so
// the card DOM survives and the pseudo-element stays painted. Released on
// pointerout when the pointer leaves the tooltip subtree, with the same
// post-release flush schedule as pointerup / keyup.
var _userHovering = false;
function _userInteracting() {
  return !!(_userPressing || _userHovering);
}
var _postPressFlushScheduled = false;
function _flushAfterPress() {
  _postPressFlushScheduled = false;
  if (_userInteracting()) return;
  if (_pendingDeltaSurfaceInvalidations) _flushDeltaSurfaceRenderBatch();
}
function _schedulePostPressFlush() {
  if (_postPressFlushScheduled) return;
  _postPressFlushScheduled = true;
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(_flushAfterPress);
  } else if (typeof setTimeout === 'function') {
    setTimeout(_flushAfterPress, 0);
  } else {
    _flushAfterPress();
  }
}
// Selector for surfaces whose DOM identity we want to preserve while the
// user's pointer is over them. Currently the agent-card tooltip is the only
// CSS-pseudo-element-keyed surface in the grid; extend this list rather
// than adding new flags if more `:hover`-driven surfaces appear.
var _TORQUE_HOVER_DEFER_SELECTOR = '.agent-card-tooltip';
function _eventTargetInHoverSurface(target) {
  if (!target || typeof target.closest !== 'function') return false;
  return !!target.closest(_TORQUE_HOVER_DEFER_SELECTOR);
}
function _hoverEdgeIsBetweenTooltips(ev) {
  // pointerover / pointerout fire on every descendant transition. We only
  // care about the boundary where the pointer enters / leaves the
  // tooltip element itself — moving between two children of the same
  // tooltip must not toggle the flag (would thrash defer on/off mid-hover).
  if (!ev || !ev.target || typeof ev.target.closest !== 'function') return false;
  var fromTooltip = ev.target.closest(_TORQUE_HOVER_DEFER_SELECTOR);
  if (!fromTooltip) return false;
  var related = ev.relatedTarget || null;
  if (related && typeof related.closest === 'function') {
    var toTooltip = related.closest(_TORQUE_HOVER_DEFER_SELECTOR);
    if (toTooltip === fromTooltip) return false;
  }
  return true;
}
// True when the current event target is a text input / textarea /
// contenteditable surface. We only want to gate renders on keydown when
// the user is actively editing a text field — global hotkeys (e.g. Cmd+B,
// Tab) should still allow renders to proceed.
function _isTextEditingTarget(target) {
  if (!target || typeof target !== 'object') return false;
  var tag = String(target.tagName || '').toLowerCase();
  if (tag === 'textarea') return true;
  if (tag === 'input') {
    var type = String(target.type || 'text').toLowerCase();
    // Treat any non-button-like input as text-editing.
    return type !== 'button' && type !== 'submit' && type !== 'reset'
      && type !== 'checkbox' && type !== 'radio'
      && type !== 'file' && type !== 'image';
  }
  return !!target.isContentEditable;
}
if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
  var _userPressEnd = function() {
    var wasInteracting = _userInteracting();
    _userPressing = false;
    if (wasInteracting && !_userInteracting() && _pendingDeltaSurfaceInvalidations) {
      // Defer flush so the browser delivers the synthetic follow-up
      // (click for pointer, input for keystroke) on the original target
      // before we swap DOM.
      _schedulePostPressFlush();
    }
  };
  var _userHoverEnd = function() {
    var wasInteracting = _userInteracting();
    _userHovering = false;
    if (wasInteracting && !_userInteracting() && _pendingDeltaSurfaceInvalidations) {
      _schedulePostPressFlush();
    }
  };
  document.addEventListener('pointerdown', function() { _userPressing = true; }, true);
  document.addEventListener('pointerup', _userPressEnd, true);
  document.addEventListener('pointercancel', _userPressEnd, true);
  // Keyboard typing in text fields: gate renders for the keystroke
  // window so the in-flight character isn't dropped by an innerHTML
  // rebuild between keydown and the corresponding `input` event.
  document.addEventListener('keydown', function(ev) {
    if (_isTextEditingTarget(ev && ev.target)) _userPressing = true;
  }, true);
  document.addEventListener('keyup', _userPressEnd, true);
  // IME composition: composition events span multiple keystrokes for
  // CJK / accented input. The renderer must not swap DOM mid-composition
  // (composer state lives on the editing element and dies with the node).
  document.addEventListener('compositionstart', function() { _userPressing = true; }, true);
  document.addEventListener('compositionend', _userPressEnd, true);
  // Hover defer: pointerover fires when the pointer enters the tooltip
  // subtree (relatedTarget is outside it); pointerout fires when it leaves
  // (relatedTarget is outside). Inner-descendant transitions are filtered
  // by `_hoverEdgeIsBetweenTooltips` so the flag doesn't thrash.
  document.addEventListener('pointerover', function(ev) {
    if (!_hoverEdgeIsBetweenTooltips(ev)) return;
    if (_eventTargetInHoverSurface(ev.target)) _userHovering = true;
  }, true);
  document.addEventListener('pointerout', function(ev) {
    if (!_hoverEdgeIsBetweenTooltips(ev)) return;
    // The edge is "outbound" when the new target is not inside the same
    // tooltip — defined by `_hoverEdgeIsBetweenTooltips` returning true
    // only on real boundary crossings. Release the flag.
    _userHoverEnd();
  }, true);
  // Safety net: clear the flag on blur in case the closing event is
  // missed (e.g. capture lost mid-press, focus stolen mid-composition).
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('blur', function() {
      _userPressEnd();
      _userHoverEnd();
    });
  }
}

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
  ws.onmessage = (e) => {
    _noteWsInbound();
    const msg = JSON.parse(e.data);
    if (typeof _compactHandleLazyResponse === 'function'
        && _compactHandleLazyResponse(msg)) {
      if (typeof renderActivePanel === 'function') renderActivePanel();
      return;
    }
    if (typeof behaviorOverlayReceiveMessage === 'function'
        && behaviorOverlayReceiveMessage(msg)) {
      return;
    }
    if (!msg.type && msg.agent_class_status && msg.agent_profile_status) {
      if (typeof agentClassManagerReceiveLaunchResult === 'function') {
        agentClassManagerReceiveLaunchResult(msg);
      }
      return;
    }
    if (msg.type === 'state') {
      _handleFullState(msg);
    } else if (msg.type === 'delta') {
      _handleDelta(msg);
    } else if (msg.type === 'focus_update') {
      _handleClientFocusUpdate(msg);
    } else if (msg.type === 'config') {
      if (msg.providers) _cachedProviders = msg.providers;
      if (msg.roles || msg.templates) _cachedAgentTemplates = _wsRoleList(msg);
      if (msg.runtime) state.runtime = msg.runtime;
      if (msg.runtime && typeof loadDaemonStatus === 'function') loadDaemonStatus();
      if (msg.runtime && typeof refreshDaemonStatusIndicator === 'function') {
        refreshDaemonStatusIndicator();
      }
      if (msg.runtime && typeof refreshStatusBar === 'function') {
        refreshStatusBar({ runtime: true });
      }
      if (_pendingModal) {
        _showAddModal(_pendingModal.mode, _pendingModal.group, msg);
        _pendingModal = null;
      }
    } else if (msg.type === 'group_settings') {
      if (msg.providers) _cachedProviders = msg.providers;
      if (msg.roles || msg.templates) _cachedAgentTemplates = _wsRoleList(msg);
      if (msg.runtime) state.runtime = msg.runtime;
      if (msg.runtime && typeof loadDaemonStatus === 'function') loadDaemonStatus();
      if (msg.runtime && typeof refreshDaemonStatusIndicator === 'function') {
        refreshDaemonStatusIndicator();
      }
      if (msg.runtime && typeof refreshStatusBar === 'function') {
        refreshStatusBar({ runtime: true });
      }
      _showGroupSettings(msg.group, msg);
    } else if (msg.type === 'toast') {
      _showToast(msg.message, msg.level);
    } else if (msg.type === 'system_banner') {
      if (typeof _applySystemBanner === 'function') {
        _applySystemBanner(msg.banner);
      }
    } else if (msg.type === 'supervisor_sessions') {
      if (typeof supervisorReceiveSessions === 'function') {
        supervisorReceiveSessions(msg);
      }
    } else if (msg.type === 'supervisor_restart') {
      if (msg.runtime) {
        state.runtime = msg.runtime;
        if (typeof loadDaemonStatus === 'function') loadDaemonStatus();
        if (typeof refreshDaemonStatusIndicator === 'function') {
          refreshDaemonStatusIndicator();
        }
        if (typeof refreshStatusBar === 'function') {
          refreshStatusBar({ runtime: true });
        }
        if (typeof healthSupervisorRuntimeReceive === 'function') {
          healthSupervisorRuntimeReceive(state.runtime && state.runtime.supervisor);
        }
      }
      if (typeof supervisorReceiveRestart === 'function') {
        supervisorReceiveRestart(msg);
      }
    } else if (msg.type === 'system_health_metrics') {
      if (typeof healthReceiveMetrics === 'function') {
        healthReceiveMetrics(msg);
      }
    } else if (msg.type === 'metrics_tick') {
      if (typeof healthMetricsReceiveTick === 'function') {
        healthMetricsReceiveTick(msg);
      }
    } else if (msg.type === 'metrics_history') {
      if (typeof healthMetricsReceiveHistory === 'function') {
        healthMetricsReceiveHistory(msg);
      }
    } else if (msg.type === 'deploy_state') {
      if (typeof statusBarReceiveDeployState === 'function') {
        statusBarReceiveDeployState(msg);
      }
    } else if (msg.type === 'mission_control_summary') {
      if (typeof missionControlReceiveSummary === 'function') {
        missionControlReceiveSummary(msg);
      }
    } else if (msg.type === 'daemon_stop') {
      if (typeof _daemonStopRequestedByUser !== 'undefined'
          && _daemonStopRequestedByUser
          && typeof _showDaemonStoppedBanner === 'function') {
        _showDaemonStoppedBanner();
      }
    } else if (msg.type === 'worktree_history') {
      _showWorktreeHistory(msg);
    } else if (msg.type === 'worktree_pr') {
      _showWorktreePR(msg);
    } else if (msg.type === 'worktree_diff_full') {
      if (typeof diffReceiveFull === 'function') diffReceiveFull(msg);
    } else if (msg.type === 'worktree_check_merge') {
      if (typeof diffReceiveMergeCheck === 'function') diffReceiveMergeCheck(msg);
    } else if (msg.type === 'worktree_merge_progress') {
      if (typeof diffReceiveMergeProgress === 'function') diffReceiveMergeProgress(msg);
    } else if (msg.type === 'worktree_merge') {
      if (typeof diffReceiveMergeResult === 'function') diffReceiveMergeResult(msg);
    } else if (msg.type === 'worktree_rebase') {
      if (typeof diffReceiveRebaseResult === 'function') diffReceiveRebaseResult(msg);
    } else if (msg.type === 'actions') {
      if (typeof _boardCacheDispatchActionList === 'function') {
        _boardCacheDispatchActionList(msg);
      }
      if (typeof _boardBatchActionWaiting !== 'undefined' && _boardBatchActionWaiting) {
        _handleBoardBatchActionList(msg);
      } else if (typeof _schedModalWaiting !== 'undefined' && _schedModalWaiting) {
        _handleScheduleActionList(msg);
      } else if (typeof _taskModalWaiting !== 'undefined' && _taskModalWaiting) {
        _handleTaskActionList(msg);
      } else if (typeof _boardEligibilityActionWaiting !== 'undefined'
          && _boardEligibilityActionWaiting
          && typeof _boardHandleEligibilityActionList === 'function') {
        _boardHandleEligibilityActionList(msg);
      } else if ((typeof _panelAppVisible === 'function' && _panelAppVisible('actions'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'actions')) {
        tplEditorReceiveList(msg);
      } else {
        // Ignore unsolicited action lists after reconnect/startup.
      }
    } else if (msg.type === 'roles' || msg.type === 'templates') {
      _cachedAgentTemplates = _wsRoleList(msg);
      if (typeof _boardCacheDispatchTemplateList === 'function') {
        _boardCacheDispatchTemplateList(msg);
      }
      if (typeof _taskTemplateWaiting !== 'undefined' && _taskTemplateWaiting) {
        _handleTaskTemplateList(msg);
      } else if (typeof _boardEligibilityTemplateWaiting !== 'undefined'
          && _boardEligibilityTemplateWaiting
          && typeof _boardHandleEligibilityTemplateList === 'function') {
        _boardHandleEligibilityTemplateList(msg);
      } else if (((typeof _panelAppVisible === 'function' && _panelAppVisible('templates'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'templates'))
          && typeof agentTemplateReceiveList === 'function') {
        agentTemplateReceiveList(msg);
      } else if (((typeof _panelAppVisible === 'function' && _panelAppVisible('actions'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'actions'))
          && typeof renderTemplatesEditor === 'function') {
        renderTemplatesEditor();
      }
    } else if (msg.type === 'template_detail') {
      if (((typeof _panelAppVisible === 'function' && _panelAppVisible('templates'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'templates'))
          && typeof agentTemplateReceiveDetail === 'function') {
        agentTemplateReceiveDetail(msg);
      }
    } else if (msg.type === 'specializations') {
      state.specializations = Array.isArray(msg.specializations)
        ? msg.specializations
        : [];
      state.specializations_group = msg.group || '';
      if (typeof renderEngineerLaunchSpecializations === 'function') {
        renderEngineerLaunchSpecializations();
      }
      if (typeof renderAddEngineerSpecializations === 'function') {
        renderAddEngineerSpecializations();
      }
      if (typeof renderGsEngineerSpecializations === 'function') {
        renderGsEngineerSpecializations();
      }
      if (typeof renderEditEngineerSpecializations === 'function') {
        renderEditEngineerSpecializations();
      }
      if (typeof agentPanelRenderEngineerSpecializationsEditor === 'function') {
        agentPanelRenderEngineerSpecializationsEditor();
      }
      if (((typeof _panelAppVisible === 'function' && _panelAppVisible('templates'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'templates'))
          && typeof specializationLibraryReceiveList === 'function') {
        specializationLibraryReceiveList(msg);
      }
    } else if (msg.type === 'specialization_detail') {
      state.specialization_detail = msg.specialization || null;
      if (((typeof _panelAppVisible === 'function' && _panelAppVisible('templates'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'templates'))
          && typeof specializationLibraryReceiveDetail === 'function') {
        specializationLibraryReceiveDetail(msg);
      }
    } else if (msg.type === 'agent_profiles') {
      if (typeof agentPanelReceiveAgentProfiles === 'function') {
        agentPanelReceiveAgentProfiles(msg);
      }
      if (typeof agentClassManagerReceiveProfiles === 'function') {
        agentClassManagerReceiveProfiles(msg);
      }
    } else if (msg.type === 'agent_profile_preview') {
      if (typeof agentPanelReceiveAgentProfilePreview === 'function') {
        agentPanelReceiveAgentProfilePreview(msg);
      }
    } else if (msg.type === 'agent_profile_assignment') {
      if (typeof agentPanelReceiveAgentProfileAssignment === 'function') {
        agentPanelReceiveAgentProfileAssignment(msg);
      }
    } else if (msg.type === 'agent_classes') {
      state.agent_classes = Array.isArray(msg.classes) ? msg.classes : [];
      state.agent_class_issues = Array.isArray(msg.issues) ? msg.issues : [];
      if (typeof agentPanelReceiveAgentClasses === 'function') {
        agentPanelReceiveAgentClasses(msg);
      }
      if (typeof agentClassManagerReceiveList === 'function') {
        agentClassManagerReceiveList(msg);
      }
    } else if (msg.type === 'agent_class_preview') {
      state.agent_class_preview = msg.agent_class || null;
      if (typeof agentPanelReceiveAgentClassPreview === 'function') {
        agentPanelReceiveAgentClassPreview(msg);
      }
      if (typeof agentClassManagerReceivePreview === 'function') {
        agentClassManagerReceivePreview(msg);
      }
    } else if (msg.type === 'agent_class_validation') {
      state.agent_class_validation = msg || null;
      state.agent_class_draft_preview = msg.agent_class || null;
      if (typeof agentClassManagerReceiveValidation === 'function') {
        agentClassManagerReceiveValidation(msg);
      }
    } else if (msg.type === 'agent_class_save' || msg.type === 'agent_class_archive' || msg.type === 'agent_class_delete') {
      state.agent_class_authoring_result = msg || null;
      if (Array.isArray(msg.classes)) {
        state.agent_classes = msg.classes;
      }
      if (Array.isArray(msg.registry_issues)) {
        state.agent_class_issues = msg.registry_issues;
      }
      if (msg.agent_class) {
        state.agent_class_preview = msg.agent_class;
      }
      if (typeof agentClassManagerReceiveMutation === 'function') {
        agentClassManagerReceiveMutation(msg);
      }
    } else if (msg.type === 'agent_class_launch') {
      state.agent_class_launch_result = msg || null;
      if (typeof agentClassManagerReceiveLaunchResult === 'function') {
        agentClassManagerReceiveLaunchResult(msg);
      }
    } else if (msg.type === 'agent_class_assignment') {
      state.agent_class_assignment = msg.status || null;
      var classStatus = msg.status || {};
      var classAgentId = classStatus.agent_id || '';
      var classCell = classAgentId && state.agents ? state.agents[classAgentId] : null;
      if (classCell) {
        classCell.agent_class_id = String(classStatus.assigned_class_id || '').trim();
        classCell.agent_class_version = String(classStatus.assigned_class_version || '').trim();
        classCell.agent_class_assigned_at = Number(classStatus.assigned_at || classCell.agent_class_assigned_at || 0) || 0;
        classCell.agent_class_assigned_by = String(classStatus.assigned_by || classCell.agent_class_assigned_by || '').trim();
        classCell.agent_class_status = classStatus;
      }
      if (typeof agentPanelReceiveAgentClassAssignment === 'function') {
        agentPanelReceiveAgentClassAssignment(msg);
      }
    } else if (msg.type === 'agent_class_status') {
      state.agent_class_status = msg.status || null;
      if (typeof agentPanelReceiveAgentClassStatus === 'function') {
        agentPanelReceiveAgentClassStatus(msg);
      }
    } else if (msg.type === 'engineer_specializations') {
      if (typeof agentPanelReceiveEngineerSpecializations === 'function') {
        agentPanelReceiveEngineerSpecializations(msg);
      } else {
        const agents = state.agents || {};
        const cell = agents[msg.engineer_id];
        if (cell) {
          cell.engineer_specializations = msg.specializations || [];
        }
      }
      if (typeof renderEditEngineerSpecializations === 'function') {
        renderEditEngineerSpecializations();
      }
    } else if (msg.type === 'template_rendered') {
      if (typeof _handleRenderedTemplate === 'function') {
        _handleRenderedTemplate(msg);
      }
    } else if (msg.type === 'action_detail') {
      if ((typeof _panelAppVisible === 'function' && _panelAppVisible('actions'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'actions')) {
        tplEditorReceiveDetail(msg);
      }
    } else if (msg.type === 'prompt_preview') {
      _showPromptPreview(msg);
    } else if (msg.type === 'system_prompt_preview') {
      if (typeof _showSystemPromptPreview === 'function') {
        _showSystemPromptPreview(msg);
      }
    } else if (msg.type === 'dispatch_action_missing') {
      _handleDispatchActionMissing(msg);
    } else if (msg.type === 'external_open') {
      if (msg.url) window.open(msg.url);
    } else if (msg.type === 'external_imported') {
      if (typeof initiativesHandleBoardTaskCreated === 'function') initiativesHandleBoardTaskCreated(msg);
      _showToast('Imported external ticket', 'info');
    } else if (msg.type === 'board_task_added') {
      if (typeof initiativesHandleBoardTaskCreated === 'function') initiativesHandleBoardTaskCreated(msg);
    } else if (msg.type === 'external_linked') {
      _showToast('External issue linked', 'info');
    } else if (msg.type === 'external_unlinked') {
      _showToast('External issue unlinked', 'info');
    } else if (msg.type === 'external_status_pushed') {
      _showToast('External status pushed', 'info');
    } else if (msg.type === 'external_comment_posted') {
      _showToast('External comment posted', 'info');
    } else if (msg.type === 'board_sync_preflight') {
      if (typeof _handleBoardSyncPreflight === 'function') _handleBoardSyncPreflight(msg);
    } else if (msg.type === 'board_sync_list_projects') {
      if (typeof _handleBoardSyncProjects === 'function') _handleBoardSyncProjects(msg);
    } else if (msg.type === 'board_sync_task') {
      if (typeof _handleBoardSyncTaskResponse === 'function') _handleBoardSyncTaskResponse(msg);
    } else if (msg.type === 'board_pull_preview') {
      if (typeof _handleBoardPullPreview === 'function') _handleBoardPullPreview(msg);
    } else if (msg.type === 'board_pull_apply') {
      if (typeof _handleBoardPullApply === 'function') _handleBoardPullApply(msg);
    } else if (msg.type === 'pipelines') {
      if (typeof tplReceivePipelines !== 'undefined') tplReceivePipelines(msg);
    } else if (msg.type === 'relay_test_result') {
      if (typeof handleRelayTestResult === 'function') handleRelayTestResult(msg);
    } else if (msg.type === 'relay_device_link') {
      if (typeof handleRelayDeviceLink === 'function') handleRelayDeviceLink(msg);
    } else if (msg.type === 'daemon_credential') {
      if (typeof handleRelayDaemonCredential === 'function') handleRelayDaemonCredential(msg);
    } else if (msg.type === 'global_settings') {
      _showGlobalSettingsModal(msg);
    } else if (msg.type === 'ai_settings') {
      if (typeof aiSettingsReceive === 'function') aiSettingsReceive(msg);
    } else if (msg.type === 'ai_settings_requires_confirmation') {
      if (typeof aiSettingsRequiresConfirmation === 'function') aiSettingsRequiresConfirmation(msg);
    } else if (msg.type === 'ai_index_job') {
      if (typeof aiIndexJobReceive === 'function') aiIndexJobReceive(msg);
    } else if (msg.type === 'memory_entries') {
      if (typeof handleContextEntries === 'function') handleContextEntries(msg);
    } else if (msg.type === 'memory_entry') {
      if (typeof handleContextEntry === 'function') handleContextEntry(msg);
    } else if (msg.type === 'initiative_list') {
      if (typeof initiativesReceiveList === 'function') initiativesReceiveList(msg);
    } else if (msg.type === 'initiative') {
      if (typeof initiativesReceiveDetail === 'function') initiativesReceiveDetail(msg);
    } else if (msg.type === 'initiative_created' || msg.type === 'initiative_updated' || msg.type === 'initiative_archived') {
      if (typeof initiativesReceiveMutation === 'function') initiativesReceiveMutation(msg);
    } else if (msg.type === 'initiative_task_linked' || msg.type === 'initiative_task_unlinked' || msg.type === 'initiative_decision_linked' || msg.type === 'initiative_decision_unlinked') {
      if (typeof initiativesReceiveLinkMutation === 'function') initiativesReceiveLinkMutation(msg);
    } else if (msg.type === 'area_list' || msg.type === 'planning_area_list') {
      if (typeof areasReceiveList === 'function') areasReceiveList(msg);
    } else if (msg.type === 'area' || msg.type === 'planning_area') {
      if (typeof areasReceiveDetail === 'function') areasReceiveDetail(msg);
    } else if (msg.type === 'area_created' || msg.type === 'area_updated' || msg.type === 'area_archived' || msg.type === 'planning_area_created' || msg.type === 'planning_area_updated' || msg.type === 'planning_area_archived') {
      if (typeof areasReceiveMutation === 'function') areasReceiveMutation(msg);
    } else if (msg.type === 'area_linked' || msg.type === 'area_unlinked' || msg.type === 'planning_area_linked' || msg.type === 'planning_area_unlinked') {
      if (typeof areasReceiveLinkMutation === 'function') areasReceiveLinkMutation(msg);
    } else if (msg.type === 'area_note_created' || msg.type === 'area_note_updated' || msg.type === 'area_note_archived' || msg.type === 'planning_area_note_created' || msg.type === 'planning_area_note_updated' || msg.type === 'planning_area_note_archived') {
      if (typeof areasReceiveNoteMutation === 'function') areasReceiveNoteMutation(msg);
    } else if (msg.type === 'scratchpad_note_list') {
      if (typeof thinkingReceiveScratchpadList === 'function') thinkingReceiveScratchpadList(msg);
    } else if (msg.type === 'scratchpad_note_created' || msg.type === 'scratchpad_note_updated' || msg.type === 'scratchpad_note_archived' || msg.type === 'scratchpad_note_deleted') {
      if (typeof thinkingReceiveScratchpadMutation === 'function') thinkingReceiveScratchpadMutation(msg);
    } else if (msg.type === 'mind_map_list') {
      if (typeof thinkingReceiveMindMapList === 'function') thinkingReceiveMindMapList(msg);
    } else if (msg.type === 'mind_map') {
      if (typeof thinkingReceiveMindMapDetail === 'function') thinkingReceiveMindMapDetail(msg);
    } else if (msg.type === 'mind_map_created' || msg.type === 'mind_map_updated' || msg.type === 'mind_map_archived' || msg.type === 'mind_map_deleted') {
      if (typeof thinkingReceiveMindMapMutation === 'function') thinkingReceiveMindMapMutation(msg);
    } else if (msg.type === 'mind_map_node_created' || msg.type === 'mind_map_node_updated' || msg.type === 'mind_map_node_positioned' || msg.type === 'mind_map_node_deleted') {
      if (typeof thinkingReceiveMindMapNodeMutation === 'function') thinkingReceiveMindMapNodeMutation(msg);
    } else if (msg.type === 'mind_map_node_reordered') {
      if (typeof thinkingReceiveMindMapNodeReordered === 'function') thinkingReceiveMindMapNodeReordered(msg);
    } else if (msg.type === 'mind_map_link_created' || msg.type === 'mind_map_link_updated' || msg.type === 'mind_map_link_deleted') {
      if (typeof thinkingReceiveMindMapLinkMutation === 'function') thinkingReceiveMindMapLinkMutation(msg);
    } else if (msg.type === 'mind_map_link_reordered') {
      if (typeof thinkingReceiveMindMapLinkReordered === 'function') thinkingReceiveMindMapLinkReordered(msg);
    } else if (msg.type === 'idea_brief_list') {
      if (!state.idea_briefs) state.idea_briefs = {};
      (msg.idea_briefs || []).forEach(function(brief) {
        if (brief && brief.id) state.idea_briefs[brief.id] = Object.assign({}, brief);
      });
      if (typeof ideaBriefReceiveList === 'function') ideaBriefReceiveList(msg);
    } else if (msg.type === 'idea_brief'
        || msg.type === 'idea_brief_created'
        || msg.type === 'idea_brief_updated'
        || msg.type === 'idea_brief_refined'
        || msg.type === 'idea_brief_parked'
        || msg.type === 'idea_brief_archived'
        || msg.type === 'idea_brief_proposed') {
      if (!state.idea_briefs) state.idea_briefs = {};
      var ideaBrief = msg.idea_brief || (msg.type === 'idea_brief' ? msg : null);
      if (ideaBrief && ideaBrief.id) state.idea_briefs[ideaBrief.id] = Object.assign({}, state.idea_briefs[ideaBrief.id] || {}, ideaBrief);
      if (typeof ideaBriefReceiveMutation === 'function') ideaBriefReceiveMutation(msg);
    } else if (msg.type === 'error') {
      if (typeof healthMetricsReceiveHistory === 'function'
          && typeof healthMetricsState !== 'undefined'
          && healthMetricsState
          && healthMetricsState.historyLoading
          && !((typeof healthState !== 'undefined' && healthState && healthState.loading))
          && ((typeof _panelAppVisible === 'function' && _panelAppVisible('health'))
            || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'health'))) {
        healthMetricsReceiveHistory(msg);
        return;
      }
      if (typeof healthReceiveMetrics === 'function'
          && typeof healthState !== 'undefined'
          && healthState
          && healthState.loading
          && ((typeof _panelAppVisible === 'function' && _panelAppVisible('health'))
            || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'health'))) {
        healthReceiveMetrics(msg);
        return;
      }
      if (typeof missionControlHandleError === 'function' && missionControlHandleError(msg)) return;
      if (typeof areasHandleError === 'function' && areasHandleError(msg)) return;
      if (typeof initiativesHandleError === 'function' && initiativesHandleError(msg)) return;
      if (typeof ideaBriefHandleError === 'function' && ideaBriefHandleError(msg)) return;
      var systemPromptErrorHandled = false;
      if (typeof _showSystemPromptPreviewError === 'function') {
        systemPromptErrorHandled = _showSystemPromptPreviewError(msg);
      }
      var specializationEditorErrorHandled = false;
      if (!systemPromptErrorHandled
          && typeof agentPanelHandleEngineerSpecializationsError === 'function') {
        specializationEditorErrorHandled = agentPanelHandleEngineerSpecializationsError(msg);
      }
      var agentProfileErrorHandled = false;
      if (!systemPromptErrorHandled
          && !specializationEditorErrorHandled
          && typeof agentPanelHandleAgentProfileError === 'function') {
        agentProfileErrorHandled = agentPanelHandleAgentProfileError(msg);
      }
      var agentClassErrorHandled = false;
      if (!systemPromptErrorHandled
          && !specializationEditorErrorHandled
          && !agentProfileErrorHandled
          && typeof agentPanelHandleAgentClassError === 'function') {
        agentClassErrorHandled = agentPanelHandleAgentClassError(msg);
      }
      if (!systemPromptErrorHandled
          && !specializationEditorErrorHandled
          && !agentProfileErrorHandled
          && !agentClassErrorHandled
          && typeof agentClassManagerHandleError === 'function') {
        agentClassErrorHandled = agentClassManagerHandleError(msg);
      }
      if (!systemPromptErrorHandled && !specializationEditorErrorHandled && !agentProfileErrorHandled && !agentClassErrorHandled) {
        if (typeof aiSettingsHandleError === 'function' && aiSettingsHandleError(msg)) return;
        if (typeof thinkingHandleError === 'function' && thinkingHandleError(msg)) return;
        if (typeof handleContextError === 'function') handleContextError(msg);
        else if (typeof _showToast === 'function' && msg.message) _showToast(msg.message, 'error');
      }
    } else if (msg.type === 'events_page') {
      if (typeof handleEventsPage === 'function') handleEventsPage(msg);
    } else if (msg.type === 'cell_events') {
      if (typeof agentPanelReceiveCellEvents === 'function') agentPanelReceiveCellEvents(msg);
    } else if (msg.type === 'mcp_calls') {
      if (typeof agentPanelReceiveMcpCalls === 'function') agentPanelReceiveMcpCalls(msg);
    } else if (msg.type === 'architect_peers') {
      if (typeof agentPanelReceiveArchitectPeerList === 'function') agentPanelReceiveArchitectPeerList(msg);
    } else if (msg.type === 'agent_message_history') {
      if (!state.agent_message_history) state.agent_message_history = {};
      state.agent_message_history[msg.agent_id] = Array.isArray(msg.history)
        ? msg.history
        : [];
    } else if (msg.type === 'architect_journal_entries') {
      if (typeof agentPanelReceiveArchitectJournal === 'function') agentPanelReceiveArchitectJournal(msg);
    } else if (msg.type === 'agent_history_list') {
      if (typeof agentHistoryReceiveList === 'function') agentHistoryReceiveList(msg);
    } else if (msg.type === 'agent_history_detail') {
      if (typeof agentHistoryReceiveDetail === 'function') agentHistoryReceiveDetail(msg);
      if (typeof taskHistoryReceiveDetail === 'function') taskHistoryReceiveDetail(msg);
    } else if (msg.type === 'action') {
      handleAction(msg);
    } else if (msg.type === 'engineer_session_map') {
      _handleEngineerSessionMapMessage(msg);
    }
  };
}

function _handleEngineerSessionMapMessage(msg) {
  if (!state.engineer_session_maps) state.engineer_session_maps = {};
  var group = (msg && msg.group) || '';
  if (!group) return;
  var engineerId = String((msg && msg.engineer_id) || '').trim();
  var key = engineerId ? (group + '::' + engineerId) : group;
  state.engineer_session_maps[key] = (msg && msg.session_map) || {};
  if (typeof _engineerReceiveSessionMap === 'function') {
    _engineerReceiveSessionMap(msg);
    return;
  }
  if (((typeof _panelAppVisible === 'function' && _panelAppVisible('engineer'))
      || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'engineer'))
      && typeof renderAgentPanel === 'function') {
    var currentGroup = (typeof _currentGroup === 'function') ? _currentGroup() : '';
    if (!currentGroup || currentGroup === group) renderAgentPanel();
  }
}

function _isTauriMode() {
  const api = (typeof window !== 'undefined' && window.nativeApi)
    || (typeof nativeApi !== 'undefined' && nativeApi)
    || null;
  if (!api || typeof api.available !== 'function') return false;
  try {
    return !!api.available();
  } catch (_) {
    return false;
  }
}

function _applyRuntimeMode() {
  const embedded = !!(state && state.runtime && state.runtime.embedded_terminal);
  const mode = (typeof _torqueUiMode === 'function')
    ? _torqueUiMode()
    : (embedded ? 'standalone' : 'toolbelt');
  const standalone = mode === 'standalone' || mode === 'desktop';
  if (!document.body) return;
  document.body.classList.toggle('runtime-embedded', embedded);
  document.body.classList.toggle('standalone-mode', standalone);
  document.body.classList.toggle('tauri-mode', _isTauriMode());
  if (document.body.dataset) {
    document.body.dataset.torqueMode = mode;
  }
}

function _maybeTriggerAgentDoneFlourish(previousTask, nextTask) {
  if (!previousTask || !nextTask) return;
  if ((previousTask.lane || '') === 'Done' || (nextTask.lane || '') !== 'Done') return;
  const agentId = nextTask.agent_id || previousTask.agent_id || '';
  if (!agentId || typeof _startAgentDoneFlourish !== 'function') return;
  _startAgentDoneFlourish(agentId, 'Done');
}

function _triggerDoneFlourishesFromTaskSnapshot(previousTasks, nextTasks) {
  const prior = previousTasks || {};
  const next = nextTasks || {};
  for (const [taskId, nextTask] of Object.entries(next)) {
    _maybeTriggerAgentDoneFlourish(prior[taskId] || null, nextTask || null);
  }
}

function _applyFocusUpdatePayload(payload) {
  payload = payload || {};
  const clientScoped = !!payload.client_scoped;
  if (!clientScoped && _clientScopedFocusActive) {
    return false;
  }
  if (clientScoped) {
    _clientScopedFocusActive = true;
  }
  var prevActive = state.active_session_id;
  if ('active_session_id' in payload) {
    state.active_session_id = payload.active_session_id;
  }
  if ('current_window_id' in payload) {
    state.current_window_id = payload.current_window_id;
  }
  if (state.active_session_id !== prevActive) {
    _syncSelectionToActiveSession();
  }
  return true;
}

function _handleClientFocusUpdate(msg) {
  _applyFocusUpdatePayload(msg || {});
  if (dragInProgress) return;
  if (typeof _queueDeltaSurfaceRender === 'function') {
    _queueDeltaSurfaceRender({ main: true, context: true });
  } else if (typeof render === 'function') {
    render();
  }
}

/* -- Full state (initial connect + resync) -------------------------------- */

function _handleFullState(msg) {
  _cancelPendingDeltaSurfaceRender();
  const prevActive = state.active_session_id;
  const prevTasks = state.board_tasks || {};
  const prevGroup = (typeof _activeGroup === 'function') ? _activeGroup() : '';
  const prevStandaloneVisibleApps = (typeof _standaloneVisiblePanelApps === 'function'
    && typeof _standalonePanelsEnabled === 'function'
    && _standalonePanelsEnabled())
    ? _standaloneVisiblePanelApps().slice()
    : [];
  const shouldRestorePanel = typeof _panelStateRestored !== 'undefined'
    ? !_panelStateRestored
    : false;
  _resyncPending = false;
  _awaitingFullState = false;
  _clientScopedFocusActive = !!(msg && msg.client_scoped_focus);
  state = msg;
  if (typeof _compactInitDeferredMaps === 'function') _compactInitDeferredMaps();
  if (typeof _invalidateTaskLookupIndex === 'function') _invalidateTaskLookupIndex();
  if (typeof _agentPanelWorkerTaskIdCacheByAgent !== 'undefined') {
    _agentPanelWorkerTaskIdCacheByAgent = {};
  }
  if (typeof _agentPanelInvalidateArchitectDecisionCache === 'function') {
    _agentPanelInvalidateArchitectDecisionCache();
  }
  if (typeof _agentPanelInvalidateArchitectMessageCache === 'function') {
    _agentPanelInvalidateArchitectMessageCache();
  }
  if (typeof _agentPanelInvalidateArchitectPeerListCache === 'function') {
    _agentPanelInvalidateArchitectPeerListCache();
  }
  _applyRuntimeMode();
  if (typeof _standalonePanelSetLayoutFromState === 'function'
      && typeof _standalonePanelsEnabled === 'function'
      && _standalonePanelsEnabled()
      && !shouldRestorePanel) {
    if (typeof _restoreStandalonePanelState === 'function') {
      _restoreStandalonePanelState({ persistResolved: false });
    } else {
      _standalonePanelSetLayoutFromState(
        (state && state.standalone_panel_layout && Object.keys(state.standalone_panel_layout).length)
          ? state.standalone_panel_layout
          : _migrateStandalonePanelLayoutFromLegacyState(),
        { fromServer: true }
      );
    }
    if (typeof _syncVisibleStandalonePanelApps === 'function') {
      _syncVisibleStandalonePanelApps(prevStandaloneVisibleApps);
    }
  }
  if (typeof _boardFiltersByGroup !== 'undefined') _boardFiltersByGroup = null;
  if (typeof _boardSelectedLanesByGroup !== 'undefined') _boardSelectedLanesByGroup = null;
  if (typeof _boardHiddenWideLanesByGroup !== 'undefined') _boardHiddenWideLanesByGroup = null;
  if (typeof _boardSavedViewsByGroup !== 'undefined') _boardSavedViewsByGroup = null;
  if (typeof _boardLaneSortsByGroup !== 'undefined') _boardLaneSortsByGroup = null;
  if (typeof _boardCardDensityByGroup !== 'undefined') _boardCardDensityByGroup = null;
  if (typeof _boardFilterStateGroup !== 'undefined') _boardFilterStateGroup = '';
  if (typeof _boardSelectedLaneStateGroup !== 'undefined') _boardSelectedLaneStateGroup = '';
  if (typeof _boardResetRenderCaches === 'function') _boardResetRenderCaches();
  if (msg.providers) _cachedProviders = msg.providers;
  if (!state.panel_events) state.panel_events = [];
  if (!state.agent_digest_settings) state.agent_digest_settings = {};
  if (!state.digest_buffer_stats) state.digest_buffer_stats = {};
  if (!state.digest_sent_events) state.digest_sent_events = {};
  if (!state.engineer_buffer_stats) state.engineer_buffer_stats = {};
  if (!state.engineer_sent_events) state.engineer_sent_events = {};
  if (!state.engineer_worklog) state.engineer_worklog = {};
  if (!state.engineer_streams) state.engineer_streams = {};
  if (!state.engineer_session_maps) state.engineer_session_maps = {};
  if (!state.mcp_calls) state.mcp_calls = {};
  if (typeof behaviorOverlayNormalizeState === 'function') {
    behaviorOverlayNormalizeState();
  } else {
    if (!state.behavior_overlay_active || typeof state.behavior_overlay_active !== 'object') {
      state.behavior_overlay_active = {};
    }
    if (!state.behavior_overlay_proposals || typeof state.behavior_overlay_proposals !== 'object') {
      state.behavior_overlay_proposals = {};
    }
    if (!state.behavior_overlay_versions || typeof state.behavior_overlay_versions !== 'object') {
      state.behavior_overlay_versions = {};
    }
  }
  if (!state.agent_message_history) state.agent_message_history = {};
  if (!state.direct_messages_by_agent) state.direct_messages_by_agent = {};
  state.agent_peer_threads = _sortAgentPeerThreadMap(state.agent_peer_threads || {});
  if (typeof state.active_group !== 'string') {
    state.active_group = '';
  }
  if (typeof state.selected_principal_id !== 'string') {
    state.selected_principal_id = '';
  }
  if (typeof state.selected_agent_id !== 'string') {
    state.selected_agent_id = '';
  }
  var restoredSelectedAgentId = '';
  var persistedActiveGroup = String(state.active_group || '').trim();
  if (typeof _lastPersistedActiveGroup !== 'undefined') {
    _lastPersistedActiveGroup = persistedActiveGroup;
  }
  if (persistedActiveGroup) {
    if (typeof _pendingActiveGroup !== 'undefined') _pendingActiveGroup = '';
    if (typeof _writeStoredActiveGroup === 'function') {
      _writeStoredActiveGroup(persistedActiveGroup);
    }
  }
  var activeSessionSelection = _selectedAgentSelectionForActiveSession();
  var urlSelectedAgentId = (typeof _agentFocusUrlTargetAgentId === 'function')
    ? _agentFocusUrlTargetAgentId()
    : '';
  if (urlSelectedAgentId && typeof _agentFocusApplyUrlSelection === 'function') {
    restoredSelectedAgentId = _agentFocusApplyUrlSelection({
      activeSessionSelection: activeSessionSelection,
    });
  } else if (!urlSelectedAgentId
      && typeof _agentFocusRestorePersistedSelection === 'function') {
    restoredSelectedAgentId = _agentFocusRestorePersistedSelection({
      activeSessionSelection: activeSessionSelection,
    });
  }
  var preferActiveTerminalSelection = !!(
    activeSessionSelection
    && activeSessionSelection.agentId
    && activeSessionSelection.cell
    && activeSessionSelection.cell.cell_type === 'terminal'
  );
  if (!restoredSelectedAgentId && preferActiveTerminalSelection) {
    restoredSelectedAgentId = _applySelectedAgentFromServer(
      activeSessionSelection.terminalId || activeSessionSelection.agentId,
      { syncGroup: true, persist: false },
    );
  } else if (!restoredSelectedAgentId
      && state.selected_agent_id
      && state.agents
      && state.agents[state.selected_agent_id]
      && !(typeof _isTombstonedAgent === 'function'
        && _isTombstonedAgent(state.agents[state.selected_agent_id]))
      && (!persistedActiveGroup
        || String(state.agents[state.selected_agent_id].group || '') === persistedActiveGroup)) {
    restoredSelectedAgentId = _applySelectedAgentFromServer(
      state.selected_agent_id,
      { syncGroup: !persistedActiveGroup, persist: false },
    );
  } else if (!restoredSelectedAgentId && state.selected_agent_id) {
    _applySelectedAgentFromServer('');
    if (persistedActiveGroup) state.active_group = persistedActiveGroup;
  }
  if (!state.window_bounds || typeof state.window_bounds !== 'object') {
    state.window_bounds = {};
  }
  if (typeof state.workspace_sidebar_width !== 'number') {
    var _workspaceWidth = Number(state.workspace_sidebar_width);
    state.workspace_sidebar_width = Number.isFinite(_workspaceWidth) ? _workspaceWidth : 0;
  }
  if (typeof state.terminal_direct_messages_height !== 'number') {
    var _terminalDirectMessagesHeight = Number(state.terminal_direct_messages_height);
    state.terminal_direct_messages_height = Number.isFinite(_terminalDirectMessagesHeight)
      ? Math.max(0, _terminalDirectMessagesHeight)
      : 0;
  }
  if (typeof state.engineer_panel_split_fraction !== 'number') {
    var _splitFraction = Number(state.engineer_panel_split_fraction);
    state.engineer_panel_split_fraction = Number.isFinite(_splitFraction) ? _splitFraction : 0.30;
  }
  if (typeof state.context_panel_split_ratio !== 'number') {
    var _contextSplitRatio = Number(state.context_panel_split_ratio);
    state.context_panel_split_ratio = Number.isFinite(_contextSplitRatio) ? _contextSplitRatio : 0.38;
  }
  if (!state.supervisor_panel_state || typeof state.supervisor_panel_state !== 'object') {
    state.supervisor_panel_state = {};
  }
  if (typeof _applyEmbeddedTerminalScrollbackFromSettings === 'function') {
    _applyEmbeddedTerminalScrollbackFromSettings();
  }
  if (typeof loadDaemonStatus === 'function') loadDaemonStatus();
  if (typeof refreshDaemonStatusIndicator === 'function') refreshDaemonStatusIndicator();
  // Relay-connection indicator: top-level `relay_connection` is captured into
  // `state` via the `state = msg` assignment above; refresh the indicator from
  // it (renders nothing when the field is absent — pre-producer / community).
  if (typeof refreshRelayStatusIndicator === 'function') refreshRelayStatusIndicator();
  if (typeof refreshStatusBar === 'function') refreshStatusBar({ fullState: true });
  if (typeof statusBarRequestDeployState === 'function') {
    statusBarRequestDeployState({ force: true });
  }
  // Relay config + provenance (TORQUE:603 #1): top-level `state.relay_config` is
  // captured by the `state = msg` assignment above; refresh the Settings Relay
  // config sub-block from it (no-op when the section/field elements aren't
  // mounted, i.e. the modal is closed or pre-producer / community).
  if (typeof refreshRelayConfigModal === 'function') refreshRelayConfigModal();
  _triggerDoneFlourishesFromTaskSnapshot(prevTasks, state.board_tasks || {});
  if (typeof _pruneAgentDoneFlourishes === 'function') {
    _pruneAgentDoneFlourishes(state.agents || {});
  }
  const nextGroup = (typeof _activeGroup === 'function') ? _activeGroup() : '';
  const groupTransition = (prevGroup !== nextGroup
      && typeof _prepareActiveGroupStateTransition === 'function')
    ? _prepareActiveGroupStateTransition(prevGroup, nextGroup)
    : null;
  _expectedSeq = (msg.seq || 0) + 1;
  // Reset pagination state on full snapshot
  if (typeof _eventsHasMore !== 'undefined') {
    _eventsHasMore = true;
    _eventsLoading = false;
    _eventsOldestId = 0;
  }
  // Sync selection on first message (restore after restart/reconnect)
  // and whenever the active session changes
  if (state.active_session_id && !restoredSelectedAgentId &&
      (!_firstStateReceived || state.active_session_id !== prevActive)) {
    _syncSelectionToActiveSession();
  }
  _firstStateReceived = true;
  if (typeof _compactAutoHydrateOnConnect === 'function') {
    _compactAutoHydrateOnConnect();
  }
  if (!dragInProgress) {
    render();
    if (!shouldRestorePanel && typeof renderActivePanel === 'function') {
      renderActivePanel();
    }
    if (typeof _finishActiveGroupStateTransition === 'function') {
      _finishActiveGroupStateTransition(groupTransition);
    }
  }
  if (typeof _engineerResetSessionMapMeta === 'function') {
    _engineerResetSessionMapMeta({ refetchOpenMissing: true });
  }
  // Restore board panel state on first load
  if (typeof _restorePanelState === 'function') _restorePanelState();
  if (typeof maybeOpenWelcomeOnBoot === 'function') maybeOpenWelcomeOnBoot();
}

/* -- Delta patching ------------------------------------------------------- */

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
  if (typeof refreshStatusBar === 'function'
      && _statusBarDeltaNeedsRefresh(msg.ops, opGroupHints)) {
    refreshStatusBar({ delta: true });
  }
  const selectedAgentGroupSynced = _selectedAgentGroupSyncedDuringDelta;
  _selectedAgentGroupSyncedDuringDelta = false;
  const taskDeltaChanges = _collectBoardTaskDeltaChanges(msg.ops, opGroupHints);
  if (taskDeltaChanges.length
      && typeof _agentPanelInvalidateWorkerTaskCacheForDeltas === 'function') {
    _agentPanelInvalidateWorkerTaskCacheForDeltas(taskDeltaChanges);
  }
  if (taskDeltaChanges.length
      && typeof _boardPatchDispatchStateTaskDeltas === 'function') {
    _boardPatchDispatchStateTaskDeltas(taskDeltaChanges);
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

function _statusBarDeltaNeedsRefresh(ops, hints) {
  for (let i = 0; i < (ops || []).length; i++) {
    const op = ops[i] || {};
    switch (op.op) {
      case 'agent_upsert':
      case 'agent_remove':
      case 'task_upsert':
      case 'task_remove':
        return true;
      case 'global_settings_update':
        if (!_globalSettingsDeltaHasChangedKeys(op)
            || _globalSettingsDeltaChangedKeys(op).indexOf('status_bar_visibility') >= 0) {
          return true;
        }
        break;
      case 'ui_update':
        if (op.key === 'active_group'
            || op.key === 'events_dismissed_attention'
            || op.key === 'selected_agent_id') {
          return true;
        }
        break;
    }
  }
  return false;
}

function _standaloneDeltaOptimizationsEnabled() {
  return !!(
    typeof _standalonePanelsEnabled === 'function'
    && _standalonePanelsEnabled()
  );
}

function _surfaceInvalidationsAny(flags) {
  if (!flags) return false;
  for (const key in flags) {
    if (key && flags[key]) return true;
  }
  return false;
}

function _mergeSurfaceInvalidations(target, source) {
  const out = target || _blankSurfaceInvalidations();
  for (const key in (source || {})) {
    if (source[key]) out[key] = true;
    else if (!Object.prototype.hasOwnProperty.call(out, key)) out[key] = false;
  }
  return out;
}

function _renderDeltaSurfaceInvalidations(flags) {
  if (!_surfaceInvalidationsAny(flags)) return;
  var _renderStart = (typeof performance !== 'undefined' && performance && typeof performance.now === 'function')
    ? performance.now()
    : (Date.now ? Date.now() : 0);
  try {
    if (typeof renderInvalidatedSurfaces === 'function') {
      renderInvalidatedSurfaces(flags);
    } else {
      render();
    }
  } finally {
    if (typeof healthRecordFrontendRender === 'function') {
      var _renderEnd = (typeof performance !== 'undefined' && performance && typeof performance.now === 'function')
        ? performance.now()
        : (Date.now ? Date.now() : _renderStart);
      healthRecordFrontendRender(Math.max(0, _renderEnd - _renderStart), 'surface-delta');
    }
  }
}

function _cancelPendingDeltaSurfaceRender() {
  if (_pendingDeltaSurfaceRenderFrame
      && typeof cancelAnimationFrame === 'function') {
    cancelAnimationFrame(_pendingDeltaSurfaceRenderFrame);
  }
  _pendingDeltaSurfaceRenderFrame = 0;
  _pendingDeltaSurfaceInvalidations = null;
}

function _flushDeltaSurfaceRenderBatch() {
  if (!_pendingDeltaSurfaceInvalidations) return;
  // If a press is in progress (e.g. an rAF was scheduled before the user
  // pressed), keep the batch queued and re-arm the rAF after release.
  // Otherwise the rAF would replace the DOM mid-press and suppress click.
  // TORQUE:264 follow-up: same gate also applies to active hover on
  // tooltip-keyed surfaces (see `_userHovering`).
  if (_userInteracting()) {
    _pendingDeltaSurfaceRenderFrame = 0;
    return;
  }
  const flags = _pendingDeltaSurfaceInvalidations;
  _pendingDeltaSurfaceInvalidations = null;
  _pendingDeltaSurfaceRenderFrame = 0;
  if (!dragInProgress) _renderDeltaSurfaceInvalidations(flags);
}

function _queueDeltaSurfaceRender(flags) {
  if (!_surfaceInvalidationsAny(flags)) return;
  // Always coalesce when rAF is available — toolbelt mode used to bypass
  // this path and render synchronously per delta, which made the
  // worker-MCP firehose (:224) freely interleave DOM swaps with user
  // presses. The ~16 ms of latency is invisible compared to losing every
  // click.
  if (typeof requestAnimationFrame !== 'function') {
    if (_userInteracting()) {
      _pendingDeltaSurfaceInvalidations = _mergeSurfaceInvalidations(
        _pendingDeltaSurfaceInvalidations,
        flags
      );
      return;
    }
    _renderDeltaSurfaceInvalidations(flags);
    return;
  }
  _pendingDeltaSurfaceInvalidations = _mergeSurfaceInvalidations(
    _pendingDeltaSurfaceInvalidations,
    flags
  );
  if (_userInteracting()) return;
  if (_pendingDeltaSurfaceRenderFrame) return;
  _pendingDeltaSurfaceRenderFrame = requestAnimationFrame(function() {
    _flushDeltaSurfaceRenderBatch();
  });
}

function _blankSurfaceInvalidations() {
  return {
    main: false,
    board: false,
    context: false,
    events: false,
    engineer: false,
    templates: false,
    health: false,
    thinking: false,
  };
}

function _markSurface(flags) {
  for (let i = 1; i < arguments.length; i++) {
    flags[arguments[i]] = true;
  }
}

function _contextDeltaAgentId(op) {
  return String(
    (op && (op.cell_id || op.agent_id || op.id))
      || ''
  ).trim();
}

function _contextWindowPayloadFromOp(op) {
  if (!op || typeof op !== 'object') return undefined;
  if (Object.prototype.hasOwnProperty.call(op, 'context_window')) {
    return op.context_window;
  }
  const data = op.data && typeof op.data === 'object' ? op.data : null;
  if (data && Object.prototype.hasOwnProperty.call(data, 'context_window')) {
    return data.context_window;
  }
  return undefined;
}

function _providerUsagePayloadFromOp(op) {
  if (!op || typeof op !== 'object') return undefined;
  if (Object.prototype.hasOwnProperty.call(op, 'provider_usage')) {
    return op.provider_usage;
  }
  const data = op.data && typeof op.data === 'object' ? op.data : null;
  if (data && Object.prototype.hasOwnProperty.call(data, 'provider_usage')) {
    return data.provider_usage;
  }
  return undefined;
}

function _applyContextMeterDeltaUpdates(agentIds) {
  if (!Array.isArray(agentIds) || !agentIds.length) return;
  if (typeof updateAgentContextMeter !== 'function') return;
  const seen = {};
  for (let i = 0; i < agentIds.length; i++) {
    const id = String(agentIds[i] || '').trim();
    if (!id || seen[id]) continue;
    seen[id] = true;
    updateAgentContextMeter(id);
  }
}

function _collectContextMeterDeltaAgentIds(ops, hints) {
  const ids = [];
  const add = function(value) {
    value = String(value || '').trim();
    if (value && ids.indexOf(value) < 0) ids.push(value);
  };
  for (let i = 0; i < (ops || []).length; i++) {
    const op = ops[i] || {};
    if (op.op === 'context_update') {
      if (_contextWindowPayloadFromOp(op) !== undefined) add(_contextDeltaAgentId(op));
      continue;
    }
    if (op.op !== 'agent_upsert') continue;
    const hint = hints && hints[i] ? hints[i] : {};
    const previous = hint && hint.agent ? hint.agent : null;
    const next = _agentNextFromDelta(op, previous);
    if (_contextWindowPayloadFromOp(op) !== undefined
        && _agentDeltaIsContextWindowOnly(previous, next, op)) add(op.id);
  }
  return ids;
}

function _peerMessageDeltaAgentIds(op) {
  const ids = [];
  const add = function(value) {
    value = String(value || '').trim();
    if (value && ids.indexOf(value) < 0) ids.push(value);
  };
  add(op && op.agent_id);
  add(op && op.sender_id);
  add(op && op.recipient_id);
  const message = (op && op.message) || {};
  add(message.agent_id);
  add(message.sender_id);
  add(message.recipient_id);
  return ids;
}

function _directMessageDeltaMessageId(op, message) {
  return String(
    (message && (message.message_id || message.id))
      || (op && (op.message_id || op.id))
      || ''
  ).trim();
}

function _deltaTimestampValue(value) {
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || value <= 0) return 0;
    return value > 100000000000 ? value / 1000 : value;
  }
  const numeric = Number(value);
  if (Number.isFinite(numeric) && String(value || '').trim() !== '') {
    if (numeric <= 0) return 0;
    return numeric > 100000000000 ? numeric / 1000 : numeric;
  }
  const parsed = Date.parse(String(value || ''));
  return Number.isFinite(parsed) ? parsed / 1000 : 0;
}

function _sortAgentPeerThreadMap(map) {
  const items = [];
  const source = (map && typeof map === 'object') ? map : {};
  for (const key in source) {
    const thread = source[key];
    if (!thread) continue;
    const id = String((thread && thread.thread_id) || key || '').trim();
    if (!id) continue;
    items.push({ id: id, thread: Object.assign({}, thread) });
  }
  items.sort(function(a, b) {
    const at = _deltaTimestampValue(a.thread && a.thread.last_activity_at);
    const bt = _deltaTimestampValue(b.thread && b.thread.last_activity_at);
    if (at !== bt) return bt - at;
    const am = String((a.thread && a.thread.last_message_id) || '');
    const bm = String((b.thread && b.thread.last_message_id) || '');
    if (am !== bm) return bm.localeCompare(am);
    return String(b.id || '').localeCompare(String(a.id || ''));
  });
  const out = {};
  for (let i = 0; i < items.length; i++) out[items[i].id] = items[i].thread;
  return out;
}

function _terminalWorkspaceViewedAgentIdBeforeDelta() {
  if (!state || !state.agents) return '';
  let cell = null;
  if (selectedTerminalId && state.agents[selectedTerminalId]) {
    cell = state.agents[selectedTerminalId];
  }
  if (!cell && state.active_session_id) {
    for (const id in state.agents) {
      const candidate = state.agents[id];
      if (candidate && candidate.session_id === state.active_session_id) {
        cell = candidate;
        break;
      }
    }
  }
  if (!cell && selectedAgentId && state.agents[selectedAgentId]) {
    cell = state.agents[selectedAgentId];
  }
  if (!cell) return '';
  if (cell.cell_type === 'terminal') {
    const parentId = String(cell.parent_id || '').trim();
    return parentId && state.agents[parentId] ? parentId : '';
  }
  return cell.cell_type === 'agent' ? String(cell.id || '') : '';
}

function _globalSettingsDeltaChangedKeys(op) {
  return Array.isArray(op && op.changed_keys)
    ? op.changed_keys.map(function(key) { return String(key || ''); }).filter(Boolean)
    : [];
}

function _globalSettingsDeltaHasChangedKeys(op) {
  return Array.isArray(op && op.changed_keys);
}

function _globalSettingsDeltaSkipsBroadInvalidation(op) {
  if (!_globalSettingsDeltaHasChangedKeys(op)) return false;
  var changed = _globalSettingsDeltaChangedKeys(op);
  return changed.every(function(key) {
    return key === 'status_bar_visibility' || key.indexOf('ai_') === 0;
  });
}

function _deltaSurfaceInvalidations(ops, hints) {
  const flags = _blankSurfaceInvalidations();
  // TORQUE:236 v13 instrumentation: when window.__torqueDebugRender is true,
  // record which delta op type flipped flags.engineer to true so the
  // user's reproduction shows what's slipping through the gates.
  const _debug = (typeof window !== 'undefined' && window.__torqueDebugRender);
  let _engBefore = false;
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i];
    const hint = hints && hints[i] ? hints[i] : {};
    if (_debug) _engBefore = !!flags.engineer;
    switch (op.op) {
      case 'agent_upsert':
      case 'agent_remove':
        _applyAgentSurfaceInvalidation(flags, op, hint);
        break;
      case 'context_update':
        // High-frequency token telemetry updates only the affected card's
        // micro-meter via `_applyContextMeterDeltaUpdates()` after state is
        // patched. Never invalidate broad surfaces for this op.
        break;
      case 'worktree_merge_progress':
        // Consumed directly by the diff modal; no broad surface invalidation.
        break;
      case 'runtime':
        // Runtime metadata refreshes daemon status with a targeted DOM update
        // in _applyDelta; never invalidate panel/grid surfaces for it.
        break;
      case 'provider_usage':
        // Provider usage is consumed by the bottom status bar via a targeted
        // chip update only. Never invalidate broad surfaces for it.
        break;
      case 'group_update':
      case 'group_remove':
      case 'group_rename':
      case 'groups_reorder':
      case 'group_settings_update':
        _markSurface(flags, 'main', 'context', 'engineer');
        break;
      case 'global_settings_update':
        if (!_globalSettingsDeltaSkipsBroadInvalidation(op)) {
          _markSurface(flags, 'main', 'context', 'engineer');
        }
        break;
      case 'ai_settings_update':
      case 'ai_index_status_update':
      case 'ai_summary_status_update':
        // Consumed by ai_settings.js with targeted DOM updates when the Settings
        // → AI tab is open. Do not mark broad settings/panel surfaces; those
        // rerenders would clobber focus/caret in the modal.
        break;
      case 'focus_update':
        // focus_update carries PTY session/window focus
        // (`active_session_id` / `current_window_id`), not agent-panel
        // selection state. Mark only surfaces that display active-terminal
        // state (main grid indicator and terminal-related context views).
        _markSurface(flags, 'main', 'context');
        break;
      case 'task_upsert':
      case 'task_remove':
        _applyTaskSurfaceInvalidation(flags, op, hint);
        if (_taskDeltaInvalidatesInitiatives(hint && hint.task, _taskNextFromDelta(op, hint && hint.task), op)) {
          _markSurface(flags, 'initiatives');
        }
        break;
      case 'lanes_update':
      case 'schedule_upsert':
      case 'schedule_remove':
        _markSurface(flags, 'board');
        break;
      case 'event_append':
      case 'mcp_call_append': {
        _markSurface(flags, 'events');
        // Engineer panel only needs a full re-render when the append
        // belongs to the focused agent *and* the focused panel is displaying
        // the affected sub-surface. Cross-agent traffic (e.g. another worker
        // firing torque_progress while the user is reading a different agent's
        // panel) used to clobber the focused panel's DOM — destroying any
        // in-progress textarea selection / scroll anchor — even though
        // nothing the panel displayed had changed.
        const _appendFocusedId = _contextFocusedAgentBeforeDelta();
        const _appendCellId = (op.op === 'mcp_call_append')
          ? String((op.call && op.call.cell_id) || '')
          : String(op.cell_id || '');
        if (_focusAppendInvalidatesFocusPanel(_appendFocusedId, _appendCellId)) {
          _markSurface(flags, 'focus');
        }
        if (_appendFocusedId && _appendCellId
            && _appendFocusedId === _appendCellId
            && (op.op !== 'mcp_call_append'
              || _focusedAgentMcpEventsSubtabActive(_appendFocusedId))) {
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'journal_append':
      case 'journal_delete': {
        const _journalFocused = _focusedEngineerAgent();
        const _journalAuthorId = String((op && op.author_cell_id) || '');
        if (_journalFocused && _journalAuthorId
            && String(_journalFocused.id || '') === _journalAuthorId) {
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'digest_buffer_stats':
      case 'digest_sent_push':
      case 'engineer_buffer_stats':
      case 'engineer_sent_events':
      case 'engineer_worklog_append':
      case 'engineer_streams':
      case 'engineer_streams_update': {
        // These ops are scoped to a specific engineer's group. The
        // engineer panel only displays the focused engineer's stream /
        // worklog / digest data, so a delta for engineer A's group
        // shouldn't clobber engineer B's panel — high-frequency stream
        // updates were the residual firehose surviving v4 + v5.
        const _engOpGroup = String((op && op.group) || '');
        if (!_engOpGroup) {
          _markSurface(flags, 'engineer');
          break;
        }
        const _engFocused = _focusedEngineerAgent();
        if (_engFocused && String(_engFocused.group || '') === _engOpGroup) {
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'architect_journal_append': {
        // Decisions still affect the main grid (decision count badges
        // etc.). Engineer-panel refresh is only needed when the focused
        // agent is the architect this entry belongs to — otherwise the
        // panel doesn't display this data. (Frequent: architects journal
        // dozens of entries per debug session.)
        if (String((op && op.type) || '').toLowerCase() === 'decision') {
          _markSurface(flags, 'main');
        }
        const _ajFocused = _focusedEngineerAgent();
        const _ajArchId = String((op && op.architect_id) || '');
        if (_ajFocused && _ajArchId
            && String(_ajFocused.id || '') === _ajArchId) {
          _markSurface(flags, 'focus', 'engineer');
        }
        break;
      }
      case 'architect_dismissed':
      case 'architect_rehired': {
        const _archLifeFocused = _focusedEngineerAgent();
        const _archLifeId = String((op && op.architect_id) || '');
        if (_archLifeFocused && _archLifeId
            && String(_archLifeFocused.id || '') === _archLifeId) {
          _markSurface(flags, 'engineer');
        }
        if (_architectPeerRosterDeltaInvalidatesFocusedMessages(null, null, op)) {
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'peer_message_upsert':
      case 'direct_message_upsert':
      case 'direct_message_read': {
        const _pmFocused = _focusedEngineerAgent();
        const _pmIds = _peerMessageDeltaAgentIds(op);
        if (op.op !== 'peer_message_upsert') {
          const _terminalViewedAgentId = _terminalWorkspaceViewedAgentIdBeforeDelta();
          if (_terminalViewedAgentId && _pmIds.indexOf(_terminalViewedAgentId) >= 0) {
            _markSurface(flags, 'main');
          }
        }
        if (_pmFocused && _pmIds.indexOf(String(_pmFocused.id || '')) >= 0) {
          _markSurface(flags, 'focus', 'engineer');
        }
        break;
      }
      case 'agent_peer_thread_upsert':
      case 'agent_peer_thread_remove':
        _markSurface(flags, 'chat');
        break;
      case 'engineer_settings_update': {
        _markSurface(flags, 'main');
        const _esFocused = _focusedEngineerAgent();
        const _esGroup = String((op && op.group) || '');
        if (_esFocused && _esGroup
            && String(_esFocused.group || '') === _esGroup) {
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'agent_digest_update': {
        _markSurface(flags, 'main');
        const _adFocused = _contextFocusedAgentBeforeDelta();
        const _adCellId = String((op && op.cell_id) || '');
        if (_adFocused && _adCellId && _adFocused === _adCellId) {
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'decision_upsert':
      case 'decision_remove':
      case 'pending_hire_upsert':
      case 'pending_hire_resolve': {
        _markSurface(flags, 'main');
        const _dpFocused = _focusedEngineerAgent();
        let _dpArchId = String((op && op.architect_id) || '');
        // _remove / _resolve ops carry only the record id; resolve the
        // architect via the cached record so the focused-architect gate
        // still works.
        if (!_dpArchId && op && op.id) {
          const _opOpName = String(op.op || '');
          let _existing = null;
          if (_opOpName === 'decision_remove'
              && state && state.decisions) {
            _existing = state.decisions[op.id] || null;
          } else if (_opOpName === 'pending_hire_resolve'
              && state && state.pending_hires) {
            _existing = state.pending_hires[op.id] || null;
          }
          if (_existing && _existing.architect_id) {
            _dpArchId = String(_existing.architect_id || '');
          }
        }
        if (_dpFocused && _dpArchId
            && String(_dpFocused.id || '') === _dpArchId) {
          _markSurface(flags, 'focus', 'engineer');
        } else if (!_dpArchId) {
          // No way to tell which architect this belongs to — be safe and
          // refresh. (Preserves legacy behavior for ops missing the field.)
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'initiative_upsert':
      case 'initiative_link_upsert':
      case 'initiative_link_remove':
      case 'area_upsert':
      case 'area_link_upsert':
      case 'area_link_remove':
      case 'area_note_upsert':
      case 'planning_area_upsert':
      case 'planning_area_link_upsert':
      case 'planning_area_link_remove':
      case 'planning_area_note_upsert':
        _markSurface(flags, 'initiatives');
        break;
      case 'thinking_scratchpad_note_upsert':
      case 'thinking_mind_map_upsert':
      case 'thinking_mind_map_node_upsert':
      case 'thinking_mind_map_link_upsert':
        _markSurface(flags, 'thinking');
        break;
      case 'idea_brief_upsert':
        _markSurface(flags, 'thinking');
        break;
      case 'behavior_overlay_version_append':
      case 'behavior_overlay_active_update':
      case 'behavior_overlay_proposal_upsert':
      case 'behavior_overlay_proposal_resolve': {
        if (typeof behaviorOverlayDeltaInvalidatesFocusedPanel === 'function'
            && behaviorOverlayDeltaInvalidatesFocusedPanel(op)) {
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'ui_update':
        _applyUiSurfaceInvalidation(flags, op.key);
        break;
    }
    if (_debug && !_engBefore && flags.engineer) {
      try {
        const _opSummary = {
          op: op.op,
          id: op.id || '',
          group: op.group || '',
          cell_id: op.cell_id || (op.call && op.call.cell_id) || '',
          author_cell_id: op.author_cell_id || '',
          architect_id: op.architect_id || '',
        };
        console.warn('[torque render] engineer-flag set by op:',
          JSON.stringify(_opSummary));
      } catch (_e) {}
    }
  }
  return flags;
}

function _taskNextFromDelta(op, previous) {
  if (!op || op.op === 'task_remove') return null;
  const next = Object.assign({}, previous || {}, op || {});
  delete next.op;
  return next;
}

function _agentNextFromDelta(op, previous) {
  if (!op || op.op === 'agent_remove') return null;
  const next = Object.assign({}, previous || {}, op || {});
  delete next.op;
  return next;
}

function _agentDeltaChangedFields(previous, next, op) {
  const fields = {};
  const keys = {};
  for (const key in (previous || {})) keys[key] = true;
  for (const key in (next || {})) keys[key] = true;
  for (const key in (op || {})) keys[key] = true;
  delete keys.op;
  for (const key in keys) {
    const a = previous ? previous[key] : undefined;
    const b = next ? next[key] : undefined;
    if (!_deltaValuesEqual(a, b)) fields[key] = true;
  }
  return fields;
}

function _agentDeltaIsContextWindowOnly(previous, next, op) {
  if (!op || op.op !== 'agent_upsert') return false;
  if (!previous || !next) return false;
  if (!Object.prototype.hasOwnProperty.call(op, 'context_window')
      && !Object.prototype.hasOwnProperty.call(op, 'provider_usage')) return false;
  const changed = _agentDeltaChangedFields(previous, next, op);
  const allowed = {
    context_window: true,
    provider_usage: true,
    last_heartbeat_at: true,
    last_activity_at: true,
    last_event_at: true,
    last_event_text: true,
  };
  for (const key in changed) {
    if (!allowed[key]) return false;
  }
  return true;
}

function _stableDeltaValue(value) {
  if (value === undefined) return '__torque_undefined__';
  if (value === null) return null;
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch (_err) {
      return String(value);
    }
  }
  return value;
}

function _deltaValuesEqual(a, b) {
  return _stableDeltaValue(a) === _stableDeltaValue(b);
}

function _deltaObjectFieldsChanged(previous, next, candidateFields) {
  const changed = {};
  const fields = candidateFields || [];
  for (let i = 0; i < fields.length; i++) {
    const field = fields[i];
    const a = previous ? previous[field] : undefined;
    const b = next ? next[field] : undefined;
    if (!_deltaValuesEqual(a, b)) changed[field] = true;
  }
  return changed;
}

function _deltaHasChangedField(changed, fields) {
  for (let i = 0; i < fields.length; i++) {
    if (changed[fields[i]]) return true;
  }
  return false;
}

function _deltaIsDispatchStateOnly(changed) {
  let sawDispatchState = false;
  for (const key in (changed || {})) {
    if (key === 'dispatch_state') {
      sawDispatchState = true;
    } else if (key !== 'updated_at') {
      return false;
    }
  }
  return sawDispatchState;
}

function _taskTouchesGroup(previous, next, group) {
  if (!group) return true;
  const prevGroup = previous ? (previous.group || '') : '';
  const nextGroup = next ? (next.group || '') : '';
  return prevGroup === group || nextGroup === group;
}

function _agentTouchesGroup(previous, next, group) {
  if (!group) return true;
  const prevGroup = previous ? (previous.group || '') : '';
  const nextGroup = next ? (next.group || '') : '';
  return prevGroup === group || nextGroup === group;
}

function _boardCurrentGroupFilterEnabled() {
  return typeof _boardFilterByGroup === 'undefined' || !!_boardFilterByGroup;
}

function _eventsCurrentGroupFilterEnabled() {
  return typeof _eventsFilterByGroup === 'undefined' || !!_eventsFilterByGroup;
}

function _currentSurfaceGroup() {
  return (typeof _currentGroup === 'function') ? (_currentGroup() || '') : '';
}

function _taskHasHumanAskLabel(task) {
  const labels = task && Array.isArray(task.labels) ? task.labels : [];
  return !!(
    task
    && labels.indexOf('torque:human') >= 0
    && labels.indexOf('torque:non-user-ask') < 0
  );
}

function _taskIsOpenAsk(task) {
  return !!(task && _taskHasHumanAskLabel(task) && (task.lane || '') !== 'Done');
}

function _taskIsAskParent(taskId, group) {
  if (!taskId || !state || !state.board_tasks) return false;
  const tasks = state.board_tasks || {};
  for (const id in tasks) {
    const task = tasks[id];
    if (!task || task.parent_task_id !== taskId) continue;
    if (!_taskIsOpenAsk(task)) continue;
    if (group && task.group !== group) continue;
    return true;
  }
  return false;
}

function _taskDeltaChangedFields(previous, next, op) {
  const fields = {};
  const keys = {};
  for (const key in (previous || {})) keys[key] = true;
  for (const key in (next || {})) keys[key] = true;
  for (const key in (op || {})) keys[key] = true;
  delete keys.op;
  for (const key in keys) {
    const a = previous ? previous[key] : undefined;
    const b = next ? next[key] : undefined;
    if (!_deltaValuesEqual(a, b)) fields[key] = true;
  }
  return fields;
}

function _taskHasBranchBoundaryMainRelevance(task) {
  if (!task) return false;
  const boundary = task.worktree_boundary && typeof task.worktree_boundary === 'object'
    ? task.worktree_boundary
    : null;
  if (boundary && boundary.repo_root && boundary.branch) return true;
  return !!task.resume_after_boundary_task_id;
}

function _taskDeltaInvalidatesBoundaryMain(previous, next, changed) {
  if (!_taskHasBranchBoundaryMainRelevance(previous)
      && !_taskHasBranchBoundaryMainRelevance(next)) {
    return false;
  }
  if (!previous || !next) return true;
  return _deltaHasChangedField(changed, [
    'task',
    'lane',
    'status',
    'created_at',
    'updated_at',
    'lane_entered_at',
    'worktree_boundary',
    'resume_after_boundary_task_id',
  ]);
}

function _taskDeltaInvalidatesMain(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  if (!previous && !next) return false;
  const changed = _taskDeltaChangedFields(previous, next, op);
  const prevAgent = previous ? String(previous.agent_id || '') : '';
  const nextAgent = next ? String(next.agent_id || '') : '';
  if (prevAgent || nextAgent) {
    if (prevAgent !== nextAgent) return true;
    if (_deltaIsDispatchStateOnly(changed)) return false;
    return _deltaHasChangedField(changed, [
      'task',
      'lane',
      'status',
      'action_name',
      'description',
      'messages',
      'messages_thread',
      'created_at',
      'updated_at',
      'started_at',
      'worktree_boundary',
      'resume_after_boundary_task_id',
      'artifacts',
      'attachments',
    ]);
  }
  if (_taskDeltaInvalidatesBoundaryMain(previous, next, changed)) return true;
  return _deltaHasChangedField(changed, [
    'worktree_boundary',
    'resume_after_boundary_task_id',
  ]);
}

function _taskDeltaInvalidatesInitiatives(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const group = _currentSurfaceGroup();
  if (!_taskTouchesGroup(previous, next, group)) return false;
  if (!previous || !next) return true;
  const changed = _taskDeltaChangedFields(previous, next, op);
  if (_deltaIsDispatchStateOnly(changed)) return false;
  return _deltaHasChangedField(changed, [
    'id',
    'group',
    'task',
    'lane',
    'status',
    'health_state',
    'archived_at',
  ]);
}

function _taskDeltaInvalidatesBoard(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const group = _boardCurrentGroupFilterEnabled() ? _currentSurfaceGroup() : '';
  if (!_taskTouchesGroup(previous, next, group)) return false;
  if (!previous || !next) return true;
  const changed = _taskDeltaChangedFields(previous, next, op);
  if (_deltaIsDispatchStateOnly(changed)) return false;
  const alwaysFields = [
    'id',
    'group',
    'lane',
    'position',
    'parent_task_id',
    'pipeline_depth',
    'depends_on',
    'task',
    'labels',
    'action_name',
    'agent_template',
    'agent_id',
    'status',
    'health_state',
    'health_details',
    'health_since',
    'verification_mode',
    'verification_state',
    'verification_notes',
    'verification_summary',
    'completion_evidence',
    'attachments',
    'artifacts',
    'board_sync',
    'provider',
    'external_id',
    'external_url',
    'messages',
    'messages_thread',
    'created_by',
    'created_by_architect_id',
    'created_by_engineer_id',
    'created_at',
    'updated_at',
    'lane_entered_at',
    'scheduled_at',
    'worktree_boundary',
    'resume_after_boundary_task_id',
  ];
  if (_deltaHasChangedField(changed, alwaysFields)) return true;
  const searchActive = !!(typeof _boardSearchQuery !== 'undefined' && _boardSearchQuery);
  if (searchActive && _deltaHasChangedField(changed, [
    'description',
    'assigned_engineer_id',
  ])) return true;
  return false;
}

function _contextFocusedTaskBeforeDelta() {
  if (typeof _contextCurrentTask === 'function') {
    const task = _contextCurrentTask();
    return task ? String(task.id || '') : '';
  }
  if (typeof _boardFocusedTask !== 'undefined' && _boardFocusedTask) {
    return String(_boardFocusedTask || '');
  }
  return '';
}

function _contextFocusedAgentBeforeDelta() {
  if (typeof _contextCurrentAgent === 'function') {
    const agent = _contextCurrentAgent();
    return agent ? String(agent.id || '') : '';
  }
  if (typeof selectedAgentId !== 'undefined' && selectedAgentId) {
    return String(selectedAgentId || '');
  }
  if (typeof focusedItemId !== 'undefined' && focusedItemId
      && state && state.agents && state.agents[focusedItemId]) {
    return String(focusedItemId || '');
  }
  return '';
}

function _focusedAgentMcpEventsSubtabActive(agentId) {
  agentId = String(agentId || '');
  if (!agentId || !state || !state.agents) return false;
  const agent = state.agents[agentId];
  if (!agent) return false;
  if (typeof _agentPanelIsMcpSubtabActive === 'function') {
    return !!_agentPanelIsMcpSubtabActive(agent);
  }
  if (typeof _agentPanelKind !== 'function'
      || typeof _agentPanelActiveTab !== 'function'
      || typeof _agentPanelEventsInnerTab !== 'function') {
    return false;
  }
  return _agentPanelActiveTab(_agentPanelKind(agent)) === 'events'
    && _agentPanelEventsInnerTab(agent) === 'mcp';
}

function _taskPipelineRef(task) {
  return task ? String(task.pipeline_root_id || task.id || '') : '';
}

function _taskDeltaMayChangeContextCurrentTask(previous, next, focusedAgentId) {
  if (!focusedAgentId) return false;
  const prevAgent = previous ? String(previous.agent_id || '') : '';
  const nextAgent = next ? String(next.agent_id || '') : '';
  if (prevAgent === focusedAgentId || nextAgent === focusedAgentId) return true;
  return false;
}

function _taskDeltaInvalidatesContext(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const focus = (typeof _contextFocus !== 'undefined') ? String(_contextFocus || 'group') : 'group';
  const focusedTaskId = _contextFocusedTaskBeforeDelta();
  const focusedAgentId = _contextFocusedAgentBeforeDelta();
  const taskId = String((op && op.id) || (next && next.id) || (previous && previous.id) || '');
  const changed = _taskDeltaChangedFields(previous, next, op);
  if (focus === 'task') {
    if (taskId && taskId === focusedTaskId) return true;
    return _taskDeltaMayChangeContextCurrentTask(previous, next, focusedAgentId);
  }
  if (focus === 'pipeline') {
    const current = focusedTaskId && state && state.board_tasks
      ? _taskPipelineRef(state.board_tasks[focusedTaskId])
      : '';
    if (!current && _taskDeltaMayChangeContextCurrentTask(previous, next, focusedAgentId)) return true;
    if (current && (_taskPipelineRef(previous) === current || _taskPipelineRef(next) === current)) return true;
    return _deltaHasChangedField(changed, ['pipeline_root_id'])
      && _taskDeltaMayChangeContextCurrentTask(previous, next, focusedAgentId);
  }
  if (focus === 'agent') {
    return _taskDeltaMayChangeContextCurrentTask(previous, next, focusedAgentId);
  }
  if (taskId && taskId === focusedTaskId) {
    return _deltaHasChangedField(changed, ['task', 'agent_id', 'lane', 'pipeline_root_id']);
  }
  return false;
}

function _taskDeltaInvalidatesEvents(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const group = _eventsCurrentGroupFilterEnabled() ? _currentSurfaceGroup() : '';
  if (!_taskTouchesGroup(previous, next, group)) return false;
  const wasAsk = _taskIsOpenAsk(previous);
  const isAsk = _taskIsOpenAsk(next);
  if (wasAsk || isAsk) {
    const changed = _taskDeltaChangedFields(previous, next, op);
    return !previous || !next || _deltaHasChangedField(changed, [
      'group',
      'labels',
      'lane',
      'task',
      'description',
      'created_at',
      'parent_task_id',
    ]);
  }
  const taskId = String((op && op.id) || (previous && previous.id) || (next && next.id) || '');
  if (_taskIsAskParent(taskId, group)) {
    const changed = _taskDeltaChangedFields(previous, next, op);
    return !previous || !next || _deltaHasChangedField(changed, [
      'task',
      'description',
      'agent_id',
      'group',
    ]);
  }
  {
    const changed = _taskDeltaChangedFields(previous, next, op);
    if (_deltaHasChangedField(changed, ['messages_thread'])) return true;
  }
  return false;
}

function _focusedEngineerAgent() {
  if (typeof _resolveFocusedAgent === 'function') return _resolveFocusedAgent();
  if (typeof focusedItemId !== 'undefined'
      && focusedItemId
      && state
      && state.agents
      && state.agents[focusedItemId]) {
    return state.agents[focusedItemId];
  }
  return null;
}

function _focusedEngineerAgentKind(agent) {
  if (typeof _agentPanelKind === 'function') return _agentPanelKind(agent);
  return String((agent && agent.kind) || 'worker');
}

function _focusedEngineerActiveTab(kind) {
  if (typeof _agentPanelActiveTab === 'function') return _agentPanelActiveTab(kind);
  return '';
}

function _taskMessagesThreadTouchesAgent(task, agentId) {
  if (!task || !agentId) return false;
  const thread = Array.isArray(task.messages_thread) ? task.messages_thread : [];
  for (let i = 0; i < thread.length; i++) {
    const entry = thread[i] || {};
    const recipientId = String(entry.recipient_agent_id || '');
    if (recipientId && recipientId === agentId) return true;
  }
  return String(task.agent_id || '') === agentId && thread.length > 0;
}


function _focusedAgentIdForFocusPanel() {
  if (typeof selectedAgentId === 'undefined' || !selectedAgentId) return '';
  return String(selectedAgentId || '');
}

function _focusPanelAgentTouchesFocused(previous, next, op) {
  const focusedId = _focusedAgentIdForFocusPanel();
  if (!focusedId) return false;
  const agentId = String((op && op.id) || (previous && previous.id) || (next && next.id) || '');
  if (agentId && agentId === focusedId) return true;
  const prevParent = previous ? String(previous.parent_id || '') : '';
  const nextParent = next ? String(next.parent_id || '') : '';
  return prevParent === focusedId || nextParent === focusedId;
}

function _agentDeltaInvalidatesFocusPanel(previous, next, op) {
  if (!_focusPanelAgentTouchesFocused(previous, next, op)) return false;
  const focusedId = _focusedAgentIdForFocusPanel();
  const focused = focusedId && state && state.agents ? state.agents[focusedId] : null;
  const focusedGroup = String((focused && focused.group) || (previous && previous.group) || (next && next.group) || '');
  if (!focusedGroup) return true;
  return _agentTouchesGroup(previous, next, focusedGroup);
}

function _taskDeltaInvalidatesFocusPanel(previous, next, op) {
  const focusedId = _focusedAgentIdForFocusPanel();
  if (!focusedId) return false;
  const prevAgent = previous ? String(previous.agent_id || '') : '';
  const nextAgent = next ? String(next.agent_id || '') : '';
  const prevEngineer = previous ? String(previous.assigned_engineer_id || '') : '';
  const nextEngineer = next ? String(next.assigned_engineer_id || '') : '';
  return prevAgent === focusedId || nextAgent === focusedId
    || prevEngineer === focusedId || nextEngineer === focusedId;
}

function _focusAppendInvalidatesFocusPanel(focusedId, cellId) {
  focusedId = String(focusedId || '');
  cellId = String(cellId || '');
  if (!focusedId || !cellId) return false;
  if (focusedId === cellId) return true;
  const cell = state && state.agents ? state.agents[cellId] : null;
  return !!(cell && String(cell.parent_id || '') === focusedId);
}

function _taskDeltaInvalidatesEngineer(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const group = _currentSurfaceGroup();
  if (!_taskTouchesGroup(previous, next, group)) return false;
  const changed = _taskDeltaChangedFields(previous, next, op);
  if (_deltaIsDispatchStateOnly(changed)) return false;
  const focused = _focusedEngineerAgent();
  if (!focused) return false;
  const kind = _focusedEngineerAgentKind(focused);
  const tab = _focusedEngineerActiveTab(kind);
  if (kind === 'worker') {
    if (tab && tab !== 'worklog' && tab !== 'messages') return false;
    const focusedId = String(focused.id || '');
    if (tab === 'messages') {
      const changed = _taskDeltaChangedFields(previous, next, op);
      if (!_deltaHasChangedField(changed, ['messages_thread'])) return false;
      return !!(
        focusedId
        && (_taskMessagesThreadTouchesAgent(previous, focusedId)
          || _taskMessagesThreadTouchesAgent(next, focusedId))
      );
    }
    return !!(
      focusedId
      && ((previous && String(previous.agent_id || '') === focusedId)
        || (next && String(next.agent_id || '') === focusedId))
    );
  }
  if (kind === 'engineer') {
    if (tab && tab !== 'worklog' && tab !== 'queued') return false;
    const focusedId = String(focused.id || '');
    return !!(
      focusedId
      && ((previous && String(previous.assigned_engineer_id || '') === focusedId)
        || (next && String(next.assigned_engineer_id || '') === focusedId))
    );
  }
  return false;
}

function _applyTaskSurfaceInvalidation(flags, op, hint) {
  const previous = hint && hint.task ? hint.task : null;
  const next = _taskNextFromDelta(op, previous);
  if (!_standaloneDeltaOptimizationsEnabled()) {
    _markSurface(flags, 'main', 'board', 'context', 'events', 'engineer');
    if (_taskDeltaInvalidatesFocusPanel(previous, next, op)) _markSurface(flags, 'focus');
    return;
  }
  if (_taskDeltaInvalidatesMain(previous, next, op)) _markSurface(flags, 'main');
  if (_taskDeltaInvalidatesBoard(previous, next, op)) _markSurface(flags, 'board');
  if (_taskDeltaInvalidatesContext(previous, next, op)) _markSurface(flags, 'context');
  if (_taskDeltaInvalidatesEvents(previous, next, op)) _markSurface(flags, 'events');
  if (_taskDeltaInvalidatesFocusPanel(previous, next, op)) _markSurface(flags, 'focus');
  if (_taskDeltaInvalidatesEngineer(previous, next, op)) _markSurface(flags, 'engineer');
}

function _agentHasAttention(agent) {
  return !!(agent && agent.needs_attention && agent.cell_type !== 'terminal');
}

function _agentIsAskParentAgent(agentId, group) {
  if (!agentId || !state || !state.board_tasks) return false;
  const tasks = state.board_tasks || {};
  for (const id in tasks) {
    const ask = tasks[id];
    if (!_taskIsOpenAsk(ask)) continue;
    if (group && ask.group !== group) continue;
    const parentId = ask.parent_task_id || '';
    const parent = parentId ? tasks[parentId] : null;
    if (parent && String(parent.agent_id || '') === String(agentId || '')) return true;
  }
  return false;
}

function _agentDeltaInvalidatesContext(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const focusedAgentId = _contextFocusedAgentBeforeDelta();
  const agentId = String((op && op.id) || (previous && previous.id) || (next && next.id) || '');
  if (!agentId || agentId !== focusedAgentId) return false;
  return true;
}

function _agentDeltaInvalidatesEvents(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const group = _eventsCurrentGroupFilterEnabled() ? _currentSurfaceGroup() : '';
  if (!_agentTouchesGroup(previous, next, group)) return false;
  const wasAttention = _agentHasAttention(previous);
  const isAttention = _agentHasAttention(next);
  if (wasAttention || isAttention) {
    const changed = _deltaObjectFieldsChanged(previous, next, [
      'group',
      'name',
      'needs_attention',
      'error_message',
      'activity_detail',
      'last_event_at',
      'cell_type',
    ]);
    return !previous || !next || _deltaHasChangedField(changed, [
      'group',
      'name',
      'needs_attention',
      'error_message',
      'activity_detail',
      'last_event_at',
      'cell_type',
    ]);
  }
  const agentId = String((op && op.id) || (previous && previous.id) || (next && next.id) || '');
  if (_agentIsAskParentAgent(agentId, group)) {
    const changed = _deltaObjectFieldsChanged(previous, next, [
      'group',
      'name',
      'slug',
    ]);
    return !previous || !next || _deltaHasChangedField(changed, ['group', 'name', 'slug']);
  }
  return false;
}

function _agentDeltaInvalidatesEngineer(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const group = _currentSurfaceGroup();
  if (!_agentTouchesGroup(previous, next, group)) return false;
  const focused = _focusedEngineerAgent();
  if (!focused) return false;
  const agentId = String((op && op.id) || (previous && previous.id) || (next && next.id) || '');
  const focusedId = String(focused.id || '');
  if (agentId && focusedId && agentId === focusedId) return true;
  // Engineer-kind focus: workers list / worklog views read related workers.
  // For now, only refresh on agents the focused engineer owns. Cross-engineer
  // / cross-owner traffic in the same group used to refresh unconditionally
  // (the trailing `return true` here), which clobbered the focused panel's
  // textarea + scroll on every worker activity pulse — the dominant firehose
  // surviving the TORQUE:236 v4 mcp/event surface gate.
  const focusedKind = _focusedEngineerAgentKind(focused);
  if (focusedKind === 'architect') {
    const hiredPrev = previous ? String(previous.hired_by_architect_id || '') : '';
    const hiredNext = next ? String(next.hired_by_architect_id || '') : '';
    if (focusedId && (hiredPrev === focusedId || hiredNext === focusedId)) {
      return true;
    }
  }
  if (focusedKind === 'engineer' || focusedKind === 'architect') {
    const ownerPrev = previous ? String(previous.owner_engineer_id || '') : '';
    const ownerNext = next ? String(next.owner_engineer_id || '') : '';
    if (focusedId && (ownerPrev === focusedId || ownerNext === focusedId)) {
      return true;
    }
  }
  return false;
}

function _architectPeerRosterDeltaInvalidatesFocusedMessages(previous, next, op) {
  const focused = _focusedEngineerAgent();
  if (!focused || _focusedEngineerAgentKind(focused) !== 'architect') return false;
  if (_focusedEngineerActiveTab('architect') !== 'messages') return false;
  const focusedGroup = String(focused.group || '');
  if (!focusedGroup) return false;
  if (op && (op.op === 'architect_dismissed' || op.op === 'architect_rehired')) {
    const architectId = String(op.architect_id || '');
    const architect = architectId && state && state.agents ? state.agents[architectId] : null;
    const group = String(op.group || (architect && architect.group) || '');
    return !!architectId && architectId !== String(focused.id || '') && group === focusedGroup;
  }
  const prevKind = String((previous && previous.kind) || '');
  const nextKind = String((next && next.kind) || '');
  if (prevKind !== 'architect' && nextKind !== 'architect') return false;
  const agentId = String((op && op.id) || (previous && previous.id) || (next && next.id) || '');
  if (agentId && agentId === String(focused.id || '')) return false;
  return _agentTouchesGroup(previous, next, focusedGroup);
}

function _applyAgentSurfaceInvalidation(flags, op, hint) {
  const previous = hint && hint.agent ? hint.agent : null;
  const next = _agentNextFromDelta(op, previous);
  if (_agentDeltaIsContextWindowOnly(previous, next, op)) {
    return;
  }
  if (!_standaloneDeltaOptimizationsEnabled()) {
    _markSurface(flags, 'main', 'context', 'events', 'engineer');
    if (_agentDeltaInvalidatesFocusPanel(previous, next, op)) _markSurface(flags, 'focus');
    return;
  }
  _markSurface(flags, 'main');
  if (_agentDeltaInvalidatesContext(previous, next, op)) _markSurface(flags, 'context');
  if (_agentDeltaInvalidatesEvents(previous, next, op)) _markSurface(flags, 'events');
  if (_agentDeltaInvalidatesFocusPanel(previous, next, op)) _markSurface(flags, 'focus');
  if (_agentDeltaInvalidatesEngineer(previous, next, op)) _markSurface(flags, 'engineer');
  if (_architectPeerRosterDeltaInvalidatesFocusedMessages(previous, next, op)) {
    _markSurface(flags, 'engineer');
  }
}

function _applyUiSurfaceInvalidation(flags, key) {
  if (key === 'standalone_panel_layout') {
    _markSurface(flags, 'board', 'chat', 'actions', 'context', 'events', 'engineer', 'templates', 'history', 'initiatives', 'thinking');
  }
  if (key === 'active_group') {
    _markSurface(flags, 'main', 'board', 'actions', 'context', 'events', 'engineer', 'templates', 'history', 'initiatives', 'thinking');
  }
  if (key === 'workspace_sidebar_width') {
    _markSurface(flags, 'main', 'board', 'chat', 'actions', 'context', 'events', 'engineer', 'templates', 'history', 'initiatives', 'thinking');
  }
  if (key === 'terminal_direct_messages_height') {
    _markSurface(flags, 'main');
  }
  if (key === 'context_panel_split_ratio') {
    _markSurface(flags, 'context');
  }
  if (key === 'supervisor_panel_state') {
    _markSurface(flags, 'supervisor');
  }
  if (key === 'events_dismissed_attention') {
    _markSurface(flags, 'events');
  }
  if (key === 'board_filters_by_group'
      || key === 'board_selected_lanes_by_group'
      || key === 'board_hidden_wide_lanes_by_group'
      || key === 'board_saved_views_by_group'
      || key === 'board_lane_sorts_by_group'
      || key === 'board_card_density_by_group') {
    _markSurface(flags, 'board');
  }
  if (key === 'selected_principal_id') {
    _markSurface(flags, 'main');
  }
  if (key === 'selected_agent_id') {
    _markSurface(flags, 'main', 'focus', 'context', 'events', 'engineer');
  }
}

function _collectSessionMapInvalidationGroups(ops, hints) {
  const groups = [];
  const seen = {};
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i] || {};
    const hint = hints && hints[i] ? hints[i] : {};
    let group = '';
    switch (op.op) {
      case 'agent_upsert':
      case 'task_upsert':
      case 'journal_append':
      case 'journal_delete':
      case 'agent_digest_update':
      case 'engineer_settings_update':
      case 'peer_message_upsert':
      case 'direct_message_upsert':
      case 'direct_message_read':
        group = op.group || '';
        break;
      case 'agent_remove':
      case 'task_remove':
        group = hint.group || '';
        break;
      default:
        group = '';
    }
    if (!group || seen[group]) continue;
    seen[group] = true;
    groups.push(group);
  }
  return groups;
}

function _surfaceUsesCurrentGroup(surface) {
  if (surface === 'board') {
    return typeof _boardFilterByGroup === 'undefined' || !!_boardFilterByGroup;
  }
  if (surface === 'events') {
    return typeof _eventsFilterByGroup === 'undefined' || !!_eventsFilterByGroup;
  }
  return surface === 'context' || surface === 'engineer' || surface === 'initiatives' || surface === 'thinking';
}

function _captureDeltaGroupHints(ops) {
  const hints = [];
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i] || {};
    let group = '';
    let task = null;
    let agent = null;
    if (op.op === 'agent_remove' && state && state.agents && state.agents[op.id]) {
      group = state.agents[op.id].group || '';
      agent = _cloneBoardDeltaTask(state.agents[op.id]);
    } else if (op.op === 'agent_upsert' && state && state.agents && state.agents[op.id]) {
      group = state.agents[op.id].group || '';
      agent = _cloneBoardDeltaTask(state.agents[op.id]);
    } else if ((op.op === 'task_remove' || op.op === 'task_upsert')
        && state && state.board_tasks && state.board_tasks[op.id]) {
      group = state.board_tasks[op.id].group || '';
      task = _cloneBoardDeltaTask(state.board_tasks[op.id]);
    }
    hints.push({ group: group, task: task, agent: agent });
  }
  return hints;
}

function _cloneBoardDeltaTask(task) {
  if (!task) return null;
  return Object.assign({}, task);
}

function _deltaOpsAreOnlyTaskDeltas(ops) {
  if (!ops || !ops.length) return false;
  for (let i = 0; i < ops.length; i++) {
    const opName = (ops[i] && ops[i].op) || '';
    if (opName !== 'task_upsert' && opName !== 'task_remove') return false;
  }
  return true;
}

function _collectBoardTaskDeltaChanges(ops, hints) {
  const changes = [];
  for (let i = 0; i < (ops || []).length; i++) {
    const op = ops[i] || {};
    if (op.op !== 'task_upsert' && op.op !== 'task_remove') continue;
    const id = op.id || '';
    const hint = hints && hints[i] ? hints[i] : {};
    const previous = hint.task || null;
    const next = (op.op === 'task_remove' || !state || !state.board_tasks)
      ? null
      : _cloneBoardDeltaTask(state.board_tasks[id]);
    changes.push({
      op: op.op,
      id: id,
      previous: previous,
      next: next,
    });
  }
  return changes;
}

function _opTouchesGroup(op, group, hint) {
  if (!op || !group) return true;
  const hintedGroup = (hint && hint.group) ? hint.group : '';
  switch (op.op) {
    case 'agent_upsert':
    case 'task_upsert':
    case 'event_append':
    case 'mcp_call_append':
      return (op.group || '') === group || (!!hintedGroup && hintedGroup === group);
    case 'agent_remove':
    case 'task_remove':
      return hintedGroup ? hintedGroup === group : true;
    case 'group_update':
    case 'group_remove':
    case 'group_settings_update':
      return (op.name || '') === group;
    case 'group_rename':
      return op.old_name === group || op.new_name === group;
    case 'journal_append':
    case 'journal_delete':
    case 'agent_digest_update':
    case 'digest_buffer_stats':
    case 'digest_sent_push':
    case 'engineer_buffer_stats':
    case 'engineer_sent_events':
    case 'engineer_worklog_append':
    case 'engineer_streams':
    case 'engineer_streams_update':
    case 'engineer_settings_update':
    case 'peer_message_upsert':
    case 'direct_message_upsert':
    case 'direct_message_read':
    case 'thinking_scratchpad_note_upsert':
    case 'thinking_mind_map_upsert':
    case 'idea_brief_upsert':
      return (op.group || op.group_name || '') === group;
    default:
      return true;
  }
}

function _opsAffectCurrentSurfaceGroup(surface, group, ops, hints) {
  if (!surface || !_surfaceUsesCurrentGroup(surface) || !group) return true;
  for (let i = 0; i < ops.length; i++) {
    if (_opTouchesGroup(ops[i], group, hints && hints[i])) return true;
  }
  return false;
}

function _applyDelta(ops) {
  for (const op of ops) {
    switch (op.op) {
      case 'agent_upsert': {
        const id = op.id;
        const previousAgent = state.agents[id] ? Object.assign({}, state.agents[id]) : null;
        if (state.agents[id]) {
          Object.assign(state.agents[id], op);
        } else {
          state.agents[id] = Object.assign({}, op);
        }
        // Clean the 'op' key from the agent data
        delete state.agents[id].op;
        if (!Object.prototype.hasOwnProperty.call(op, 'agent_class_status')) {
          const carriesAgentClassFields = Object.prototype.hasOwnProperty.call(op, 'agent_class_id')
            || Object.prototype.hasOwnProperty.call(op, 'agent_class_version')
            || Object.prototype.hasOwnProperty.call(op, 'effective_agent_class_id')
            || Object.prototype.hasOwnProperty.call(op, 'effective_agent_class_version')
            || Object.prototype.hasOwnProperty.call(op, 'effective_agent_class_snapshot')
            || Object.prototype.hasOwnProperty.call(op, 'effective_agent_class_applied_at');
          if (carriesAgentClassFields) delete state.agents[id].agent_class_status;
        }
        if (Object.prototype.hasOwnProperty.call(op, 'mcp_messages')
            && typeof _agentPanelInvalidateArchitectMessageCache === 'function') {
          _agentPanelInvalidateArchitectMessageCache(id);
          if (previousAgent && previousAgent.id && previousAgent.id !== id) {
            _agentPanelInvalidateArchitectMessageCache(previousAgent.id);
          }
        }
        if (typeof _agentPanelInvalidateArchitectPeerListCache === 'function') {
          var prevKind = String((previousAgent && previousAgent.kind) || '');
          var nextKind = String((state.agents[id] && state.agents[id].kind) || '');
          var groupChanged = previousAgent
            && String(previousAgent.group || '') !== String((state.agents[id] && state.agents[id].group) || '');
          if (prevKind === 'architect' || nextKind === 'architect' || groupChanged) {
            _agentPanelInvalidateArchitectPeerListCache();
          }
        }
        break;
      }
      case 'context_update': {
        const id = _contextDeltaAgentId(op);
        if (!id || !state.agents || !state.agents[id]) break;
        const payload = _contextWindowPayloadFromOp(op);
        const providerUsage = _providerUsagePayloadFromOp(op);
        if (payload === undefined && providerUsage === undefined) break;
        if (payload !== undefined) {
          state.agents[id].context_window = (payload && typeof payload === 'object')
            ? Object.assign({}, payload)
            : {};
        }
        if (providerUsage !== undefined) {
          state.agents[id].provider_usage = (providerUsage && typeof providerUsage === 'object')
            ? Object.assign({}, providerUsage)
            : providerUsage;
        }
        break;
      }
      case 'agent_remove':
        var removedAgent = state.agents ? state.agents[op.id] : null;
        delete state.agents[op.id];
        if (state.agent_digest_settings) delete state.agent_digest_settings[op.id];
        if (state.digest_buffer_stats) delete state.digest_buffer_stats[op.id];
        if (state.digest_sent_events) delete state.digest_sent_events[op.id];
        if (state.agent_message_history) delete state.agent_message_history[op.id];
        if (state.direct_messages_by_agent) delete state.direct_messages_by_agent[op.id];
        if (typeof _agentPanelInvalidateArchitectPeerListCache === 'function'
            && (!removedAgent || String(removedAgent.kind || '') === 'architect')) {
          _agentPanelInvalidateArchitectPeerListCache();
        }
        // Selection/focus globals are browser-local — the server doesn't know
        // about them. Selections can be cleared immediately; focusedItemId is
        // left intact until render() can use previous grid-row metadata to pick
        // the next logical focus target.
        if (typeof selectedAgentId !== 'undefined' && selectedAgentId === op.id) {
          selectedAgentId = null;
        }
        if (typeof selectedTerminalId !== 'undefined' && selectedTerminalId === op.id) {
          selectedTerminalId = null;
        }
        // Keep focusedItemId until render() rebuilds the grid navigation model.
        // The renderer has the previous row metadata and can fall forward to the
        // next logical cell instead of dropping keyboard focus on every remove.
        if (typeof _clearAgentDoneFlourish === 'function') {
          _clearAgentDoneFlourish(op.id);
        }
        break;

      case 'agent_message_history_append': {
        if (!state.agent_message_history) state.agent_message_history = {};
        var historyAgentId = String(op.agent_id || '');
        var historyEntry = op.entry || null;
        if (historyAgentId && historyEntry) {
          if (!Array.isArray(state.agent_message_history[historyAgentId])) {
            state.agent_message_history[historyAgentId] = [];
          }
          state.agent_message_history[historyAgentId].unshift(historyEntry);
          var historyLimit = Number(op.limit || 100);
          if (!Number.isFinite(historyLimit) || historyLimit < 1) historyLimit = 100;
          if (state.agent_message_history[historyAgentId].length > historyLimit) {
            state.agent_message_history[historyAgentId].length = historyLimit;
          }
        }
        break;
      }

      case 'peer_message_upsert': {
        var peerMessage = Object.assign({}, op.message || op.entry || {});
        delete peerMessage.op;
        var peerAgentIds = String(op.agent_id || '').trim()
          ? [String(op.agent_id || '').trim()]
          : _peerMessageDeltaAgentIds(op);
        if (!peerAgentIds.length && peerMessage.peer_id) {
          peerAgentIds.push(String(peerMessage.peer_id || ''));
        }
        if (!state.agents) state.agents = {};
        for (var pmi = 0; pmi < peerAgentIds.length; pmi++) {
          var peerAgentId = String(peerAgentIds[pmi] || '');
          var peerAgent = state.agents[peerAgentId];
          if (!peerAgent) continue;
          if (!Array.isArray(peerAgent.mcp_messages)) peerAgent.mcp_messages = [];
          var peerMessageId = String(peerMessage.id || op.id || '');
          var replacedPeerMessage = false;
          if (peerMessageId) {
            for (var pmj = 0; pmj < peerAgent.mcp_messages.length; pmj++) {
              if (String((peerAgent.mcp_messages[pmj] || {}).id || '') === peerMessageId) {
                peerAgent.mcp_messages[pmj] = Object.assign({}, peerAgent.mcp_messages[pmj], peerMessage);
                replacedPeerMessage = true;
                break;
              }
            }
          }
          if (!replacedPeerMessage) peerAgent.mcp_messages.unshift(Object.assign({}, peerMessage));
          if (peerAgent.mcp_messages.length > 50) peerAgent.mcp_messages.length = 50;
          if (typeof _agentPanelInvalidateArchitectMessageCache === 'function') {
            _agentPanelInvalidateArchitectMessageCache(peerAgentId);
          }
        }
        break;
      }

      case 'direct_message_upsert':
      case 'direct_message_read': {
        var directMessage = Object.assign({}, op.message || op.entry || {});
        delete directMessage.op;
        var directAgentIds = String(op.agent_id || '').trim()
          ? [String(op.agent_id || '').trim()]
          : _peerMessageDeltaAgentIds(op);
        if (!state.direct_messages_by_agent) state.direct_messages_by_agent = {};
        for (var dmi = 0; dmi < directAgentIds.length; dmi++) {
          var directAgentId = String(directAgentIds[dmi] || '').trim();
          if (!directAgentId) continue;
          if (!Array.isArray(state.direct_messages_by_agent[directAgentId])) {
            state.direct_messages_by_agent[directAgentId] = [];
          }
          var directMessageId = _directMessageDeltaMessageId(op, directMessage);
          if (directMessageId) {
            if (!directMessage.id) directMessage.id = directMessageId;
            if (!directMessage.message_id) directMessage.message_id = directMessageId;
          }
          var replacedDirectMessage = false;
          if (directMessageId) {
            for (var dmj = 0; dmj < state.direct_messages_by_agent[directAgentId].length; dmj++) {
              var existingDirectId = _directMessageDeltaMessageId(
                {},
                state.direct_messages_by_agent[directAgentId][dmj] || {}
              );
              if (existingDirectId === directMessageId) {
                state.direct_messages_by_agent[directAgentId][dmj] = Object.assign(
                  {},
                  state.direct_messages_by_agent[directAgentId][dmj],
                  directMessage,
                  op.op === 'direct_message_read' ? { read_at: op.read_at || directMessage.read_at || 0 } : {}
                );
                replacedDirectMessage = true;
                break;
              }
            }
          }
          if (!replacedDirectMessage && directMessageId) {
            state.direct_messages_by_agent[directAgentId].push(Object.assign({}, directMessage));
          }
          state.direct_messages_by_agent[directAgentId].sort(function(a, b) {
            var at = _deltaTimestampValue(a && (a.created_at || a.timestamp || a.sent_at));
            var bt = _deltaTimestampValue(b && (b.created_at || b.timestamp || b.sent_at));
            if (at !== bt) return at - bt;
            return _directMessageDeltaMessageId({}, a || {}).localeCompare(
              _directMessageDeltaMessageId({}, b || {})
            );
          });
          var directLimit = Number(op.limit || 100);
          if (!Number.isFinite(directLimit) || directLimit < 1) directLimit = 100;
          if (state.direct_messages_by_agent[directAgentId].length > directLimit) {
            state.direct_messages_by_agent[directAgentId] =
              state.direct_messages_by_agent[directAgentId].slice(-directLimit);
          }
        }
        break;
      }

      case 'agent_peer_thread_upsert': {
        if (!state.agent_peer_threads || typeof state.agent_peer_threads !== 'object') {
          state.agent_peer_threads = {};
        }
        var chatThread = Object.assign({}, op.thread || {});
        var chatThreadId = String(op.thread_id || chatThread.thread_id || '').trim();
        if (chatThreadId && op.thread) {
          if (!chatThread.thread_id) chatThread.thread_id = chatThreadId;
          state.agent_peer_threads[chatThreadId] = chatThread;
          state.agent_peer_threads = _sortAgentPeerThreadMap(state.agent_peer_threads);
        }
        break;
      }

      case 'agent_peer_thread_remove': {
        if (state.agent_peer_threads && op.thread_id) {
          delete state.agent_peer_threads[String(op.thread_id || '')];
          state.agent_peer_threads = _sortAgentPeerThreadMap(state.agent_peer_threads);
        }
        break;
      }

      case 'group_update':
        state.groups[op.name] = op.agents;
        break;
      case 'group_remove':
        delete state.groups[op.name];
        delete state.group_settings[op.name];
        if (state.engineer_buffer_stats) delete state.engineer_buffer_stats[op.name];
        if (state.engineer_sent_events) delete state.engineer_sent_events[op.name];
        if (state.engineer_worklog) delete state.engineer_worklog[op.name];
        if (state.engineer_streams) delete state.engineer_streams[op.name];
        if (state.engineer_session_maps) {
          Object.keys(state.engineer_session_maps).forEach(function(key) {
            if (key === op.name || key.indexOf(op.name + '::') === 0) {
              delete state.engineer_session_maps[key];
            }
          });
        }
        break;
      case 'group_rename': {
        if (state.groups[op.old_name]) {
          state.groups[op.new_name] = state.groups[op.old_name];
          delete state.groups[op.old_name];
        }
        if (state.group_settings && state.group_settings[op.old_name]) {
          state.group_settings[op.new_name] = state.group_settings[op.old_name];
          delete state.group_settings[op.old_name];
        }
        if (state.engineer_buffer_stats && state.engineer_buffer_stats[op.old_name]) {
          state.engineer_buffer_stats[op.new_name] = state.engineer_buffer_stats[op.old_name];
          delete state.engineer_buffer_stats[op.old_name];
        }
        if (state.engineer_sent_events && state.engineer_sent_events[op.old_name]) {
          state.engineer_sent_events[op.new_name] = state.engineer_sent_events[op.old_name];
          delete state.engineer_sent_events[op.old_name];
        }
        if (state.engineer_worklog && state.engineer_worklog[op.old_name]) {
          state.engineer_worklog[op.new_name] = state.engineer_worklog[op.old_name];
          delete state.engineer_worklog[op.old_name];
        }
        if (state.engineer_streams && state.engineer_streams[op.old_name]) {
          state.engineer_streams[op.new_name] = state.engineer_streams[op.old_name];
          delete state.engineer_streams[op.old_name];
        }
        if (state.engineer_session_maps) {
          Object.keys(state.engineer_session_maps).forEach(function(key) {
            if (key === op.old_name) {
              state.engineer_session_maps[op.new_name] = state.engineer_session_maps[key];
              delete state.engineer_session_maps[key];
            } else if (key.indexOf(op.old_name + '::') === 0) {
              var nextKey = op.new_name + key.slice(String(op.old_name).length);
              state.engineer_session_maps[nextKey] = state.engineer_session_maps[key];
              delete state.engineer_session_maps[key];
            }
          });
        }
        break;
      }
      case 'groups_reorder': {
        const newGroups = {};
        for (const name of op.groups) {
          newGroups[name] = state.groups[name] || [];
        }
        state.groups = newGroups;
        break;
      }
      case 'group_settings_update': {
        const name = op.name;
        if (!state.group_settings) state.group_settings = {};
        if (!state.group_settings[name]) state.group_settings[name] = {};
        Object.assign(state.group_settings[name], op);
        delete state.group_settings[name].op;
        delete state.group_settings[name].name;
        break;
      }

      case 'task_upsert': {
        const id = op.id;
        if (!state.board_tasks) state.board_tasks = {};
        const previousTask = state.board_tasks[id]
          ? Object.assign({}, state.board_tasks[id])
          : null;
        if (state.board_tasks[id]) {
          Object.assign(state.board_tasks[id], op);
        } else {
          state.board_tasks[id] = Object.assign({}, op);
        }
        delete state.board_tasks[id].op;
        if (typeof _invalidateTaskLookupIndex === 'function') _invalidateTaskLookupIndex();
        _maybeTriggerAgentDoneFlourish(previousTask, state.board_tasks[id]);
        break;
      }
      case 'task_remove':
        if (state.board_tasks) delete state.board_tasks[op.id];
        if (typeof _invalidateTaskLookupIndex === 'function') _invalidateTaskLookupIndex();
        break;

      case 'lanes_update':
        state.board_lanes = op.lanes;
        break;

      case 'global_settings_update': {
        const gs = Object.assign({}, op);
        delete gs.op;
        delete gs.changed_keys;
        state.global_settings = gs;
        if (typeof invalidateEffectiveKeybindings === 'function') {
          invalidateEffectiveKeybindings();
        }
        if (typeof _syncKeybindingSettingsFromGlobal === 'function') {
          _syncKeybindingSettingsFromGlobal(gs);
        }
        if (typeof _syncStatusBarSettingsFromGlobal === 'function') {
          _syncStatusBarSettingsFromGlobal(gs);
        }
        if (typeof _applyEmbeddedTerminalScrollbackFromSettings === 'function') {
          _applyEmbeddedTerminalScrollbackFromSettings();
        }
        break;
      }

      case 'ai_settings_update':
        if (typeof aiSettingsApplyDelta === 'function') aiSettingsApplyDelta(op);
        break;

      case 'ai_index_status_update':
        if (typeof aiIndexStatusApplyDelta === 'function') aiIndexStatusApplyDelta(op);
        break;

      case 'ai_summary_status_update':
        if (typeof aiSummaryStatusApplyDelta === 'function') aiSummaryStatusApplyDelta(op);
        break;

      case 'event_append': {
        if (!state.panel_events) state.panel_events = [];
        var evt = Object.assign({}, op);
        delete evt.op;
        // Replace existing event by ID (for replace_last updates)
        var replaced = false;
        if (evt.id) {
          for (var ei = state.panel_events.length - 1; ei >= 0; ei--) {
            if (state.panel_events[ei].id === evt.id) {
              state.panel_events[ei] = evt;
              replaced = true;
              break;
            }
          }
        }
        if (!replaced) state.panel_events.push(evt);
        if (state.panel_events.length > 500)
          state.panel_events = state.panel_events.slice(-500);
        break;
      }

      case 'mcp_call_append': {
        var call = op.call || {};
        if (typeof agentPanelReceiveMcpCallAppend === 'function') {
          agentPanelReceiveMcpCallAppend(call);
        } else {
          if (String(call.hook_event_name || '') !== 'PostToolUse') break;
          if (!state.mcp_calls) state.mcp_calls = {};
          var callCellId = String(call.cell_id || '');
          if (callCellId) {
            if (!state.mcp_calls[callCellId]) state.mcp_calls[callCellId] = [];
            state.mcp_calls[callCellId].unshift(call);
            if (state.mcp_calls[callCellId].length > 500) {
              state.mcp_calls[callCellId].length = 500;
            }
          }
        }
        break;
      }

      case 'worktree_merge_progress':
        if (typeof diffReceiveMergeProgress === 'function') {
          diffReceiveMergeProgress(op);
        }
        break;

      case 'schedule_upsert': {
        if (!state.schedules) state.schedules = {};
        const sid = op.id;
        if (state.schedules[sid]) {
          Object.assign(state.schedules[sid], op);
        } else {
          state.schedules[sid] = Object.assign({}, op);
        }
        delete state.schedules[sid].op;
        break;
      }
      case 'schedule_remove':
        if (state.schedules) delete state.schedules[op.id];
        break;

      case 'ui_update':
        var prevStandaloneVisibleApps = (op.key === 'standalone_panel_layout'
          && typeof _standaloneVisiblePanelApps === 'function'
          && typeof _standalonePanelsEnabled === 'function'
          && _standalonePanelsEnabled())
          ? _standaloneVisiblePanelApps().slice()
          : [];
        state[op.key] = op.value;
        if (op.key === 'selected_agent_id') {
          _applySelectedAgentFromServer(op.value || '');
        }
        if (op.key === 'standalone_panel_layout'
            && typeof _standalonePanelSetLayoutFromState === 'function') {
          _standalonePanelSetLayoutFromState(op.value || {}, { fromServer: true });
          if (typeof _syncVisibleStandalonePanelApps === 'function') {
            _syncVisibleStandalonePanelApps(prevStandaloneVisibleApps);
          }
        }
        if (op.key === 'active_group') {
          if (typeof _lastPersistedActiveGroup !== 'undefined') {
            _lastPersistedActiveGroup = String(op.value || '');
          }
          if (typeof _writeStoredActiveGroup === 'function') {
            _writeStoredActiveGroup(op.value || '');
          }
          if (typeof _pendingActiveGroup !== 'undefined'
              && _pendingActiveGroup === String(op.value || '')) {
            _pendingActiveGroup = '';
          }
        }
        if (op.key === 'workspace_sidebar_width'
            && typeof _applyWorkspaceSidebarWidth === 'function') {
          _applyWorkspaceSidebarWidth(op.value || 0);
        }
        if (op.key === 'context_panel_split_ratio'
            && typeof _contextApplyPersistedSplit === 'function') {
          _contextApplyPersistedSplit();
        }
        if (op.key === 'supervisor_panel_state'
            && typeof supervisorApplyPersistedUiState === 'function') {
          supervisorApplyPersistedUiState(op.value || {});
        }
        if (op.key === 'board_filters_by_group'
            && typeof _boardFiltersByGroup !== 'undefined') {
          _boardFiltersByGroup = null;
          if (typeof _boardFilterStateGroup !== 'undefined') {
            _boardFilterStateGroup = '';
          }
        }
        if (op.key === 'board_selected_lanes_by_group'
            && typeof _boardSelectedLanesByGroup !== 'undefined') {
          _boardSelectedLanesByGroup = null;
          if (typeof _boardSelectedLaneStateGroup !== 'undefined') {
            _boardSelectedLaneStateGroup = '';
          }
        }
        if (op.key === 'board_hidden_wide_lanes_by_group'
            && typeof _boardHiddenWideLanesByGroup !== 'undefined') {
          _boardHiddenWideLanesByGroup = null;
          if (typeof _boardLaneRenderCache !== 'undefined') {
            _boardLaneRenderCache = {};
          }
        }
        if (op.key === 'board_saved_views_by_group'
            && typeof _boardSavedViewsByGroup !== 'undefined') {
          _boardSavedViewsByGroup = null;
        }
        if (op.key === 'board_lane_sorts_by_group'
            && typeof _boardLaneSortsByGroup !== 'undefined') {
          _boardLaneSortsByGroup = null;
        }
        if (op.key === 'board_card_density_by_group'
            && typeof _boardCardDensityByGroup !== 'undefined') {
          _boardCardDensityByGroup = null;
        }
        break;

      case 'journal_append': {
        if (!state.engineer_journal) state.engineer_journal = {};
        var authorId = String((op && op.author_cell_id) || '');
        if (authorId) {
          if (!state.engineer_journal[authorId]) state.engineer_journal[authorId] = [];
          var je = Object.assign({}, op);
          delete je.op;
          state.engineer_journal[authorId].unshift(je);
          // Cap at 200 entries per engineer
          if (state.engineer_journal[authorId].length > 200)
            state.engineer_journal[authorId] = state.engineer_journal[authorId].slice(0, 200);
        }
        break;
      }

      case 'journal_delete': {
        var authorDel = String((op && op.author_cell_id) || '');
        if (authorDel && state.engineer_journal && state.engineer_journal[authorDel]) {
          state.engineer_journal[authorDel] = state.engineer_journal[authorDel].filter(
            function(e) { return e.id !== op.id; });
        } else if (state.engineer_journal) {
          Object.keys(state.engineer_journal).forEach(function(key) {
            var bucket = state.engineer_journal[key];
            if (!Array.isArray(bucket)) return;
            state.engineer_journal[key] = bucket.filter(function(e) {
              return e.id !== op.id;
            });
          });
        }
        break;
      }

      case 'architect_journal_append': {
        var archId = op.architect_id || '';
        if (archId) {
          if (!state.architect_journals) state.architect_journals = {};
          if (!state.architect_journals[archId]) state.architect_journals[archId] = [];
          var entry = Object.assign({}, op);
          delete entry.op;
          var bucket = state.architect_journals[archId];
          bucket.unshift(entry);
          if (bucket.length > 500) bucket.length = 500;
          if (typeof _agentPanelArchitectJournalDidPrepend === 'function') {
            _agentPanelArchitectJournalDidPrepend(archId);
          }
          if (typeof _agentPanelInvalidateArchitectJournalCache === 'function') {
            _agentPanelInvalidateArchitectJournalCache(archId);
          }
        }
        break;
      }

      case 'architect_dismissed':
      case 'architect_rehired':
        if (state && state.agents && op.architect_id && state.agents[op.architect_id]) {
          state.agents[op.architect_id].dismissed_at = (
            op.op === 'architect_dismissed' ? (op.dismissed_at || Date.now() / 1000) : 0
          );
        }
        if (typeof _agentPanelInvalidateArchitectPeerListCache === 'function') {
          _agentPanelInvalidateArchitectPeerListCache();
        }
        break;

      case 'engineer_buffer_stats': {
        if (!state.engineer_buffer_stats) state.engineer_buffer_stats = {};
        var bsg = op.group || '';
        if (bsg) {
          state.engineer_buffer_stats[bsg] = {
            buffered_events: op.buffered_events || 0,
            next_push_in: op.next_push_in || 0,
            next_push_at: op.next_push_at || 0,
            queued_events: op.queued_events || [],
            manual_flush_requested: !!op.manual_flush_requested,
          };
        }
        break;
      }

      case 'engineer_sent_events': {
        if (!state.engineer_sent_events) state.engineer_sent_events = {};
        var wsg = op.group || '';
        if (wsg) {
          state.engineer_sent_events[wsg] = op.events || [];
        }
        break;
      }

      case 'digest_buffer_stats': {
        if (!state.digest_buffer_stats) state.digest_buffer_stats = {};
        var dbg = op.agent_id || '';
        if (dbg) {
          state.digest_buffer_stats[dbg] = {
            agent_id: dbg,
            group: op.group || '',
            buffered_events: op.buffered_events || 0,
            next_push_in: op.next_push_in || 0,
            next_push_at: op.next_push_at || 0,
            queued_events: op.queued_events || [],
            manual_flush_requested: !!op.manual_flush_requested,
          };
        }
        break;
      }

      case 'digest_sent_push': {
        if (!state.digest_sent_events) state.digest_sent_events = {};
        var dsg = op.agent_id || '';
        if (dsg) {
          state.digest_sent_events[dsg] = op.events || [];
        }
        break;
      }

      case 'engineer_worklog_append': {
        if (!state.engineer_worklog) state.engineer_worklog = {};
        var wlg = op.group || '';
        if (wlg) {
          if (!state.engineer_worklog[wlg]) state.engineer_worklog[wlg] = [];
          var worklogEntry = Object.assign({}, op.entry || {});
          state.engineer_worklog[wlg].unshift(worklogEntry);
          if (state.engineer_worklog[wlg].length > 200) {
            state.engineer_worklog[wlg] = state.engineer_worklog[wlg].slice(0, 200);
          }
        }
        break;
      }

      case 'engineer_streams':
      case 'engineer_streams_update': {
        if (!state.engineer_streams) state.engineer_streams = {};
        var wstg = op.group || '';
        if (wstg) {
          if (Object.prototype.hasOwnProperty.call(op, 'streams')) {
            state.engineer_streams[wstg] = op.streams;
          } else if (Array.isArray(op.items)) {
            state.engineer_streams[wstg] = { items: op.items };
          } else {
            state.engineer_streams[wstg] = [];
          }
        }
        break;
      }

      case 'engineer_settings_update': {
        if (!state.engineer_settings) state.engineer_settings = {};
        var wg = op.group || '';
        if (wg) {
          var ws = Object.assign({}, op);
          delete ws.op;
          state.engineer_settings[wg] = ws;
        }
        break;
      }

      case 'agent_digest_update': {
        if (!state.agent_digest_settings) state.agent_digest_settings = {};
        var digestAgentId = op.agent_id || '';
        if (digestAgentId) {
          var digestSettings = Object.assign({}, op);
          delete digestSettings.op;
          state.agent_digest_settings[digestAgentId] = digestSettings;
        }
        break;
      }

      case 'initiative_upsert': {
        if (!state.initiatives) state.initiatives = {};
        var initiative = Object.assign({}, op);
        delete initiative.op;
        if (initiative.id) state.initiatives[initiative.id] = Object.assign({}, state.initiatives[initiative.id] || {}, initiative);
        break;
      }

      case 'initiative_link_upsert':
      case 'initiative_link_remove': {
        if (typeof initiativesLoadDetail === 'function' && op.initiative_id && typeof _initiativesSelectedId !== 'undefined' && _initiativesSelectedId === op.initiative_id) {
          initiativesLoadDetail(op.initiative_id, { force: true });
        }
        break;
      }

      case 'area_upsert':
      case 'planning_area_upsert': {
        if (!state.areas) state.areas = {};
        var area = Object.assign({}, op);
        delete area.op;
        if (area.id) state.areas[area.id] = Object.assign({}, state.areas[area.id] || {}, area);
        break;
      }

      case 'area_link_upsert':
      case 'area_link_remove':
      case 'planning_area_link_upsert':
      case 'planning_area_link_remove': {
        if (typeof areasLoadDetail === 'function' && op.area_id && typeof _areasSelectedId !== 'undefined' && _areasSelectedId === op.area_id) {
          areasLoadDetail(op.area_id, { force: true });
        }
        break;
      }

      case 'area_note_upsert':
      case 'planning_area_note_upsert': {
        if (typeof areasLoadDetail === 'function' && op.area_id && typeof _areasSelectedId !== 'undefined' && _areasSelectedId === op.area_id) {
          areasLoadDetail(op.area_id, { force: true });
        }
        break;
      }

      case 'thinking_scratchpad_note_upsert': {
        if (!state.thinking) state.thinking = { scratchpad_notes: {}, mind_maps: {} };
        if (!state.thinking.scratchpad_notes) state.thinking.scratchpad_notes = {};
        var thinkingNote = Object.assign({}, op);
        delete thinkingNote.op;
        if (thinkingNote.id) state.thinking.scratchpad_notes[thinkingNote.id] = Object.assign({}, state.thinking.scratchpad_notes[thinkingNote.id] || {}, thinkingNote);
        if (typeof thinkingReceiveScratchpadDelta === 'function') thinkingReceiveScratchpadDelta(thinkingNote);
        break;
      }

      case 'thinking_mind_map_upsert': {
        if (!state.thinking) state.thinking = { scratchpad_notes: {}, mind_maps: {} };
        if (!state.thinking.mind_maps) state.thinking.mind_maps = {};
        var thinkingMap = Object.assign({}, op);
        delete thinkingMap.op;
        if (thinkingMap.id) state.thinking.mind_maps[thinkingMap.id] = Object.assign({}, state.thinking.mind_maps[thinkingMap.id] || {}, thinkingMap);
        if (typeof thinkingReceiveMindMapDelta === 'function') thinkingReceiveMindMapDelta(thinkingMap);
        break;
      }

      case 'thinking_mind_map_node_upsert': {
        var thinkingNode = Object.assign({}, op);
        delete thinkingNode.op;
        if (typeof thinkingReceiveMindMapNodeDelta === 'function') thinkingReceiveMindMapNodeDelta(thinkingNode);
        break;
      }

      case 'thinking_mind_map_link_upsert': {
        var thinkingLink = Object.assign({}, op);
        delete thinkingLink.op;
        if (typeof thinkingReceiveMindMapLinkDelta === 'function') thinkingReceiveMindMapLinkDelta(thinkingLink);
        break;
      }

      case 'idea_brief_upsert': {
        if (!state.idea_briefs) state.idea_briefs = {};
        var deltaIdeaBrief = Object.assign({}, op);
        delete deltaIdeaBrief.op;
        if (deltaIdeaBrief.id) {
          state.idea_briefs[deltaIdeaBrief.id] = Object.assign(
            {},
            state.idea_briefs[deltaIdeaBrief.id] || {},
            deltaIdeaBrief
          );
        }
        if (typeof ideaBriefReceiveDelta === 'function') ideaBriefReceiveDelta(deltaIdeaBrief);
        break;
      }

      case 'decision_upsert': {
        if (!state.decisions) state.decisions = {};
        var decisionId = op.id;
        if (decisionId) {
          var previousDecision = state.decisions[decisionId] || null;
          if (typeof _agentPanelInvalidateArchitectDecisionCache === 'function') {
            _agentPanelInvalidateArchitectDecisionCache(op.architect_id || (previousDecision && previousDecision.architect_id) || '');
            if (previousDecision
                && previousDecision.architect_id
                && previousDecision.architect_id !== op.architect_id) {
              _agentPanelInvalidateArchitectDecisionCache(previousDecision.architect_id);
            }
          }
          var decision = Object.assign({}, op);
          delete decision.op;
          state.decisions[decisionId] = decision;
        }
        break;
      }

      case 'decision_remove':
        if (typeof _agentPanelInvalidateArchitectDecisionCache === 'function') {
          var removedDecision = state.decisions ? state.decisions[op.id] : null;
          _agentPanelInvalidateArchitectDecisionCache(op.architect_id || (removedDecision && removedDecision.architect_id) || '');
        }
        if (state.decisions) delete state.decisions[op.id];
        break;

      case 'pending_hire_upsert': {
        if (!state.pending_hires) state.pending_hires = {};
        var pendingHireId = op.id;
        if (pendingHireId) {
          var pendingHire = Object.assign({}, op);
          delete pendingHire.op;
          state.pending_hires[pendingHireId] = pendingHire;
        }
        break;
      }

      case 'pending_hire_resolve':
        if (state.pending_hires) delete state.pending_hires[op.id];
        break;

      case 'behavior_overlay_version_append':
      case 'behavior_overlay_active_update':
      case 'behavior_overlay_proposal_upsert':
      case 'behavior_overlay_proposal_resolve':
        if (typeof behaviorOverlayApplyDelta === 'function') {
          behaviorOverlayApplyDelta(op);
        }
        break;

      case 'provider_usage': {
        if (!state.provider_usage || typeof state.provider_usage !== 'object') {
          state.provider_usage = {};
        }
        var provider = String(
          op.provider || op.provider_id || op.adapter || op.name || ''
        ).trim();
        if (!provider && op.provider_usage && typeof op.provider_usage === 'object') {
          state.provider_usage = Object.assign({}, op.provider_usage);
        } else if (provider) {
          if (op.delete || op.remove || op.value === null) {
            delete state.provider_usage[provider];
          } else {
            var usagePayload = op.usage || op.value || op.payload || op.data || null;
            if (!usagePayload || typeof usagePayload !== 'object') {
              usagePayload = Object.assign({}, op);
              delete usagePayload.op;
              delete usagePayload.provider;
              delete usagePayload.provider_id;
              delete usagePayload.adapter;
              delete usagePayload.name;
            }
            state.provider_usage[provider] = usagePayload;
          }
        } else {
          var usageMap = Object.assign({}, op);
          delete usageMap.op;
          Object.assign(state.provider_usage, usageMap);
        }
        if (typeof refreshStatusBar === 'function') {
          refreshStatusBar({ providerUsage: true });
        }
        break;
      }

      case 'runtime': {
        // Daemon-global runtime metadata and supervisor health. Patch in
        // place and refresh only targeted status surfaces; health-panel
        // supervisor metrics update only when the Health panel is visible.
        var runtimePayload = Object.assign({}, op);
        delete runtimePayload.op;
        if (state.runtime && typeof state.runtime === 'object') {
          for (var runtimeKey in state.runtime) {
            if (Object.prototype.hasOwnProperty.call(state.runtime, runtimeKey)
                && !Object.prototype.hasOwnProperty.call(runtimePayload, runtimeKey)) {
              delete state.runtime[runtimeKey];
            }
          }
          Object.assign(state.runtime, runtimePayload);
        } else {
          state.runtime = runtimePayload;
        }
        if (typeof loadDaemonStatus === 'function') loadDaemonStatus();
        if (typeof refreshDaemonStatusIndicator === 'function') {
          refreshDaemonStatusIndicator();
        }
        if (typeof refreshStatusBar === 'function') {
          refreshStatusBar({ runtime: true });
        }
        if (typeof healthSupervisorRuntimeReceive === 'function') {
          healthSupervisorRuntimeReceive(state.runtime && state.runtime.supervisor);
        }
        if (typeof supervisorReceiveRuntime === 'function') {
          supervisorReceiveRuntime(state.runtime && state.runtime.supervisor);
        }
        break;
      }

      case 'relay_connection': {
        // Daemon-global, low-frequency (state-change only). Patch
        // `state.relay_connection` in place + refresh ONLY the relay
        // indicator (+ modal row if open). Deliberately NOT handled in
        // `_deltaSurfaceInvalidations` — it must never mark a panel/grid
        // surface (surface-invalidation discipline, CLAUDE.md).
        var relayPayload = Object.assign({}, op);
        delete relayPayload.op;
        if (state.relay_connection && typeof state.relay_connection === 'object') {
          // Patch in place so any held reference stays valid; drop stale keys.
          for (var relayKey in state.relay_connection) {
            if (Object.prototype.hasOwnProperty.call(state.relay_connection, relayKey)
                && !Object.prototype.hasOwnProperty.call(relayPayload, relayKey)) {
              delete state.relay_connection[relayKey];
            }
          }
          Object.assign(state.relay_connection, relayPayload);
        } else {
          state.relay_connection = relayPayload;
        }
        if (typeof refreshRelayStatusIndicator === 'function') {
          refreshRelayStatusIndicator();
        }
        if (typeof refreshStatusBar === 'function') {
          refreshStatusBar({ relay: true });
        }
        break;
      }

      case 'relay_config': {
        // Resolved relay config + per-field provenance (TORQUE:603 #1, contract
        // 40c1c73e6bec). Daemon-global, low-frequency (boot + relay-settings
        // save). Patch `state.relay_config` in place + refresh ONLY the Settings
        // Relay config sub-block (if open). Like `relay_connection`, deliberately
        // NOT in `_deltaSurfaceInvalidations` — never marks a panel/grid surface
        // (surface-invalidation discipline, CLAUDE.md). The render preserves any
        // focused/dirty editable input so a delta never clobbers an in-progress
        // edit (frontend-state-preservation discipline).
        var relayConfigPayload = Object.assign({}, op);
        delete relayConfigPayload.op;
        if (state.relay_config && typeof state.relay_config === 'object') {
          for (var relayCfgKey in state.relay_config) {
            if (Object.prototype.hasOwnProperty.call(state.relay_config, relayCfgKey)
                && !Object.prototype.hasOwnProperty.call(relayConfigPayload, relayCfgKey)) {
              delete state.relay_config[relayCfgKey];
            }
          }
          Object.assign(state.relay_config, relayConfigPayload);
        } else {
          state.relay_config = relayConfigPayload;
        }
        if (typeof refreshRelayConfigModal === 'function') {
          refreshRelayConfigModal();
        }
        break;
      }

      case 'focus_update':
        _applyFocusUpdatePayload(op);
        break;
    }
  }
  // Rebuild parent→children index
  _rebuildChildren();
  if (typeof _pruneAgentDoneFlourishes === 'function') {
    _pruneAgentDoneFlourishes(state.agents || {});
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
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
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

function handleAction(msg) {
  if (msg.action === 'close_cell') {
    if (msg.cell_id) removeAgent(msg.cell_id);
  } else if (msg.action === 'add_agent') {
    if (msg.group) quickAddAgent(msg.group);
  } else if (msg.action === 'add_engineer') {
    if (typeof openAddEngineerModal === 'function') openAddEngineerModal();
  } else if (msg.action === 'add_architect') {
    if (typeof openAddArchitectModal === 'function') openAddArchitectModal(msg.group || '');
  } else if (msg.action === 'add_terminal') {
    // Manual terminal creation is no longer exposed in the operator UI. Ignore
    // stale server-side action shims rather than spawning a terminal.
    return false;
  }
}

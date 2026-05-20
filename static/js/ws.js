/* WebSocket connection and shared state */

const WS_URL = `ws://${location.host}/ws`;
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
  engineer_panel_split_fraction: 0.30,
  context_panel_split_ratio: 0.38,
  supervisor_panel_state: {},
  agent_message_history: {},
};
let dragInProgress = false;
let selectedAgentId = null;
let selectedTerminalId = null;
let focusedItemId = null;
let _cachedAgentTemplates = [];
var _selectedAgentGroupSyncedDuringDelta = false;

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
    _setConnDotState(true);
    if (typeof _clearDaemonStoppedBanner === 'function'
        && typeof _daemonStopRequestedByUser !== 'undefined'
        && _daemonStopRequestedByUser) {
      _clearDaemonStoppedBanner();
    }
    if (typeof loadDaemonStatus === 'function') loadDaemonStatus();
  };
  ws.onclose = () => {
    _resyncPending = false;
    _awaitingFullState = false;
    if (typeof _engineerResetSessionMapMeta === 'function') {
      _engineerResetSessionMapMeta({ clearStale: false });
    }
    _setConnDotState(false);
    if (typeof loadDaemonStatus === 'function') loadDaemonStatus();
    if (typeof _daemonStopRequestedByUser !== 'undefined'
        && _daemonStopRequestedByUser
        && typeof _showDaemonStoppedBanner === 'function') {
      _showDaemonStoppedBanner();
    }
    setTimeout(connect, 2000);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (typeof _compactHandleLazyResponse === 'function'
        && _compactHandleLazyResponse(msg)) {
      if (typeof renderActivePanel === 'function') renderActivePanel();
      return;
    }
    if (msg.type === 'state') {
      _handleFullState(msg);
    } else if (msg.type === 'delta') {
      _handleDelta(msg);
    } else if (msg.type === 'config') {
      if (msg.providers) _cachedProviders = msg.providers;
      if (msg.roles || msg.templates) _cachedAgentTemplates = _wsRoleList(msg);
      if (msg.runtime) state.runtime = msg.runtime;
      if (msg.runtime && typeof loadDaemonStatus === 'function') loadDaemonStatus();
      if (_pendingModal) {
        _showAddModal(_pendingModal.mode, _pendingModal.group, msg);
        _pendingModal = null;
      }
    } else if (msg.type === 'group_settings') {
      if (msg.providers) _cachedProviders = msg.providers;
      if (msg.roles || msg.templates) _cachedAgentTemplates = _wsRoleList(msg);
      if (msg.runtime) state.runtime = msg.runtime;
      if (msg.runtime && typeof loadDaemonStatus === 'function') loadDaemonStatus();
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
    } else if (msg.type === 'system_health_metrics') {
      if (typeof healthReceiveMetrics === 'function') {
        healthReceiveMetrics(msg);
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
      if (typeof renderEngineerLaunchSpecializations === 'function') {
        renderEngineerLaunchSpecializations();
      }
      if (typeof renderGsEngineerSpecializations === 'function') {
        renderGsEngineerSpecializations();
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
    } else if (msg.type === 'engineer_specializations') {
      const agents = state.agents || {};
      const cell = agents[msg.engineer_id];
      if (cell) {
        cell.engineer_specializations = msg.specializations || [];
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
      _showToast('Imported external ticket', 'info');
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
    } else if (msg.type === 'global_settings') {
      _showGlobalSettingsModal(msg);
    } else if (msg.type === 'memory_entries') {
      if (typeof handleContextEntries === 'function') handleContextEntries(msg);
    } else if (msg.type === 'memory_entry') {
      if (typeof handleContextEntry === 'function') handleContextEntry(msg);
    } else if (msg.type === 'error') {
      if (typeof healthReceiveMetrics === 'function'
          && typeof healthState !== 'undefined'
          && healthState
          && healthState.loading
          && ((typeof _panelAppVisible === 'function' && _panelAppVisible('health'))
            || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'health'))) {
        healthReceiveMetrics(msg);
        return;
      }
      var systemPromptErrorHandled = false;
      if (typeof _showSystemPromptPreviewError === 'function') {
        systemPromptErrorHandled = _showSystemPromptPreviewError(msg);
      }
      if (!systemPromptErrorHandled) {
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
  const iterm2 = mode === 'toolbelt';
  if (!document.body) return;
  document.body.classList.toggle('runtime-embedded', embedded);
  document.body.classList.toggle('standalone-mode', standalone);
  document.body.classList.toggle('iterm2-mode', iterm2);
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
  if (!state.agent_message_history) state.agent_message_history = {};
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
  var preferActiveTerminalSelection = !!(
    activeSessionSelection
    && activeSessionSelection.agentId
    && activeSessionSelection.cell
    && activeSessionSelection.cell.cell_type === 'terminal'
  );
  if (preferActiveTerminalSelection) {
    restoredSelectedAgentId = _applySelectedAgentFromServer(
      activeSessionSelection.terminalId || activeSessionSelection.agentId,
      { syncGroup: true, persist: false },
    );
  } else if (state.selected_agent_id
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
  } else if (state.selected_agent_id) {
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
  const selectedAgentGroupSynced = _selectedAgentGroupSyncedDuringDelta;
  _selectedAgentGroupSyncedDuringDelta = false;
  const taskDeltaChanges = _collectBoardTaskDeltaChanges(msg.ops, opGroupHints);
  if (taskDeltaChanges.length
      && typeof _agentPanelInvalidateWorkerTaskCacheForDeltas === 'function') {
    _agentPanelInvalidateWorkerTaskCacheForDeltas(taskDeltaChanges);
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
  if (typeof renderInvalidatedSurfaces === 'function') {
    renderInvalidatedSurfaces(flags);
  } else {
    render();
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
  };
}

function _markSurface(flags) {
  for (let i = 1; i < arguments.length; i++) {
    flags[arguments[i]] = true;
  }
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
      case 'group_update':
      case 'group_remove':
      case 'group_rename':
      case 'groups_reorder':
      case 'group_settings_update':
      case 'global_settings_update':
        _markSurface(flags, 'main', 'context', 'engineer');
        break;
      case 'focus_update':
        // TORQUE:236 v14: focus_update carries iTerm2 session/window focus
        // (`active_session_id` / `current_window_id`), NOT agent panel
        // selection state. The engineer panel renders from
        // `focusedItemId` / `selectedAgentId` (client-side); the only
        // consumer of `active_session_id` is a deep fallback in
        // `_agentPanelCurrentGroup()` that never fires when an agent is
        // focused. iTerm2's FocusMonitor emits this op every time the
        // active terminal session changes — high-frequency on user
        // interaction. Mark only the surfaces that actually display
        // active-terminal state (main grid for the active-terminal
        // indicator, context panel for terminal-related views).
        _markSurface(flags, 'main', 'context');
        break;
      case 'task_upsert':
      case 'task_remove':
        _applyTaskSurfaceInvalidation(flags, op, hint);
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
        break;
      }
      case 'peer_message_upsert': {
        const _pmFocused = _focusedEngineerAgent();
        const _pmIds = _peerMessageDeltaAgentIds(op);
        if (_pmFocused && _pmIds.indexOf(String(_pmFocused.id || '')) >= 0) {
          _markSurface(flags, 'focus', 'engineer');
        }
        break;
      }
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
  return !!(
    task
    && Array.isArray(task.labels)
    && task.labels.indexOf('torque:human') >= 0
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

function _taskDeltaInvalidatesBoard(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const group = _boardCurrentGroupFilterEnabled() ? _currentSurfaceGroup() : '';
  if (!_taskTouchesGroup(previous, next, group)) return false;
  if (!previous || !next) return true;
  const changed = _taskDeltaChangedFields(previous, next, op);
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
  if (focusedKind === 'engineer' || focusedKind === 'architect') {
    const ownerPrev = previous ? String(previous.owner_engineer_id || '') : '';
    const ownerNext = next ? String(next.owner_engineer_id || '') : '';
    if (focusedId && (ownerPrev === focusedId || ownerNext === focusedId)) {
      return true;
    }
  }
  return false;
}

function _applyAgentSurfaceInvalidation(flags, op, hint) {
  const previous = hint && hint.agent ? hint.agent : null;
  const next = _agentNextFromDelta(op, previous);
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
}

function _applyUiSurfaceInvalidation(flags, key) {
  if (key === 'standalone_panel_layout') {
    _markSurface(flags, 'board', 'actions', 'context', 'events', 'engineer', 'templates', 'history');
  }
  if (key === 'active_group') {
    _markSurface(flags, 'main', 'board', 'actions', 'context', 'events', 'engineer', 'templates', 'history');
  }
  if (key === 'workspace_sidebar_width') {
    _markSurface(flags, 'main', 'board', 'actions', 'context', 'events', 'engineer', 'templates', 'history');
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
  return surface === 'context' || surface === 'engineer';
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
      return (op.group || '') === group;
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
      case 'agent_remove':
        delete state.agents[op.id];
        if (state.agent_digest_settings) delete state.agent_digest_settings[op.id];
        if (state.digest_buffer_stats) delete state.digest_buffer_stats[op.id];
        if (state.digest_sent_events) delete state.digest_sent_events[op.id];
        if (state.agent_message_history) delete state.agent_message_history[op.id];
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
        state.global_settings = gs;
        if (typeof _applyEmbeddedTerminalScrollbackFromSettings === 'function') {
          _applyEmbeddedTerminalScrollbackFromSettings();
        }
        break;
      }

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

      case 'focus_update':
        if ('active_session_id' in op) {
          const prevActive = state.active_session_id;
          state.active_session_id = op.active_session_id;
          if (state.active_session_id !== prevActive) {
            _syncSelectionToActiveSession();
          }
        }
        if ('current_window_id' in op) {
          state.current_window_id = op.current_window_id;
        }
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
    if (msg.group && msg.parent_id) quickAddTerminal(msg.group, msg.parent_id);
  }
}

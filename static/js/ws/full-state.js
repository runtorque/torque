/* Full-state hydration and focus restoration. */

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

function _clientScopedFocusOwnsSelectedAgent() {
  return !!_clientScopedFocusActive;
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
  if (typeof _torqueBumpStateRevision === 'function') {
    _torqueBumpStateRevision();
  }
  const prevActive = state.active_session_id;
  const prevOperatorNotices = state.operator_notices || {};
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
  if (typeof inboxNormalizeState === 'function') {
    inboxNormalizeState(prevOperatorNotices);
  }
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
  if (!state.agent_message_loops) state.agent_message_loops = {};
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
  if (typeof state.terminal_compose_height !== 'number') {
    var _terminalComposeHeight = Number(state.terminal_compose_height);
    state.terminal_compose_height = Number.isFinite(_terminalComposeHeight)
      ? Math.max(0, _terminalComposeHeight)
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

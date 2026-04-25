/* WebSocket connection and shared state */

const WS_URL = `ws://${location.host}/ws`;
let ws = null;
let state = {
  agents: {},
  groups: {},
  children: {},
  active_session_id: null,
  selected_principal_id: '',
};
let dragInProgress = false;
let selectedAgentId = null;
let selectedTerminalId = null;
let focusedItemId = null;
let _cachedAgentTemplates = [];

function _wsRoleList(msg) {
  return (msg && (msg.roles || msg.templates)) || [];
}

var _firstStateReceived = false;
var _expectedSeq = 0;
var _resyncPending = false;
var _awaitingFullState = false;
var _pendingDeltaSurfaceInvalidations = null;
var _pendingDeltaSurfaceRenderFrame = 0;

function connect() {
  _firstStateReceived = false;
  _resyncPending = false;
  _awaitingFullState = false;
  var url = (typeof _compactPrepareWSUrl === 'function')
    ? _compactPrepareWSUrl(WS_URL)
    : WS_URL;
  ws = new WebSocket(url);
  ws.onopen = () => {
    document.getElementById('conn-dot').classList.add('ok');
    document.getElementById('conn-dot').title = 'Connected';
  };
  ws.onclose = () => {
    _resyncPending = false;
    _awaitingFullState = false;
    if (typeof _engineerResetSessionMapMeta === 'function') {
      _engineerResetSessionMapMeta({ clearStale: false });
    }
    document.getElementById('conn-dot').classList.remove('ok');
    document.getElementById('conn-dot').title = 'Disconnected';
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
      if (_pendingModal) {
        _showAddModal(_pendingModal.mode, _pendingModal.group, msg);
        _pendingModal = null;
      }
    } else if (msg.type === 'group_settings') {
      if (msg.providers) _cachedProviders = msg.providers;
      if (msg.roles || msg.templates) _cachedAgentTemplates = _wsRoleList(msg);
      if (msg.runtime) state.runtime = msg.runtime;
      _showGroupSettings(msg.group, msg);
    } else if (msg.type === 'toast') {
      _showToast(msg.message, msg.level);
    } else if (msg.type === 'system_banner') {
      if (typeof _applySystemBanner === 'function') {
        _applySystemBanner(msg.banner);
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
    } else if (msg.type === 'specialization_detail') {
      state.specialization_detail = msg.specialization || null;
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
    } else if (msg.type === 'pipelines') {
      if (typeof tplReceivePipelines !== 'undefined') tplReceivePipelines(msg);
    } else if (msg.type === 'global_settings') {
      _showGlobalSettingsModal(msg);
    } else if (msg.type === 'memory_entries') {
      if (typeof handleContextEntries === 'function') handleContextEntries(msg);
    } else if (msg.type === 'memory_entry') {
      if (typeof handleContextEntry === 'function') handleContextEntry(msg);
    } else if (msg.type === 'error') {
      if (typeof handleContextError === 'function') handleContextError(msg);
      else if (typeof _showToast === 'function' && msg.message) _showToast(msg.message, 'error');
    } else if (msg.type === 'events_page') {
      if (typeof handleEventsPage === 'function') handleEventsPage(msg);
    } else if (msg.type === 'cell_events') {
      if (typeof agentPanelReceiveCellEvents === 'function') agentPanelReceiveCellEvents(msg);
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
  state.engineer_session_maps[group] = (msg && msg.session_map) || {};
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

function _applyRuntimeMode() {
  const embedded = !!(state && state.runtime && state.runtime.embedded_terminal);
  document.body.classList.toggle('runtime-embedded', embedded);
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
  if (typeof _boardSavedViewsByGroup !== 'undefined') _boardSavedViewsByGroup = null;
  if (typeof _boardLaneSortsByGroup !== 'undefined') _boardLaneSortsByGroup = null;
  if (typeof _boardCardDensityByGroup !== 'undefined') _boardCardDensityByGroup = null;
  if (typeof _boardFilterStateGroup !== 'undefined') _boardFilterStateGroup = '';
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
  if (typeof state.selected_principal_id !== 'string') {
    state.selected_principal_id = '';
  }
  if (typeof _applyEmbeddedTerminalScrollbackFromSettings === 'function') {
    _applyEmbeddedTerminalScrollbackFromSettings();
  }
  _triggerDoneFlourishesFromTaskSnapshot(prevTasks, state.board_tasks || {});
  if (typeof _pruneAgentDoneFlourishes === 'function') {
    _pruneAgentDoneFlourishes(state.agents || {});
  }
  _expectedSeq = (msg.seq || 0) + 1;
  // Reset pagination state on full snapshot
  if (typeof _eventsHasMore !== 'undefined') {
    _eventsHasMore = true;
    _eventsLoading = false;
    _eventsOldestId = 0;
  }
  // Sync selection on first message (restore after restart/reconnect)
  // and whenever the active session changes
  if (state.active_session_id &&
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
  }
  if (typeof _engineerResetSessionMapMeta === 'function') {
    _engineerResetSessionMapMeta({ refetchOpenMissing: true });
  }
  // Restore board panel state on first load
  if (typeof _restorePanelState === 'function') _restorePanelState();
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
  _applyDelta(msg.ops);
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
  if (prevGroup !== nextGroup) {
    activeSurfaces.forEach(function(surface) {
      if (surface) invalidations[surface] = true;
    });
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
  const flags = _pendingDeltaSurfaceInvalidations;
  _pendingDeltaSurfaceInvalidations = null;
  _pendingDeltaSurfaceRenderFrame = 0;
  if (!dragInProgress) _renderDeltaSurfaceInvalidations(flags);
}

function _queueDeltaSurfaceRender(flags) {
  if (!_surfaceInvalidationsAny(flags)) return;
  if (!_standaloneDeltaOptimizationsEnabled()
      || typeof requestAnimationFrame !== 'function') {
    _renderDeltaSurfaceInvalidations(flags);
    return;
  }
  _pendingDeltaSurfaceInvalidations = _mergeSurfaceInvalidations(
    _pendingDeltaSurfaceInvalidations,
    flags
  );
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
  };
}

function _markSurface(flags) {
  for (let i = 1; i < arguments.length; i++) {
    flags[arguments[i]] = true;
  }
}

function _deltaSurfaceInvalidations(ops, hints) {
  const flags = _blankSurfaceInvalidations();
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i];
    const hint = hints && hints[i] ? hints[i] : {};
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
      case 'focus_update':
      case 'global_settings_update':
        _markSurface(flags, 'main', 'context', 'engineer');
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
        _markSurface(flags, 'events', 'engineer');
        break;
      case 'journal_append':
      case 'journal_delete':
      case 'architect_journal_append':
      case 'digest_buffer_stats':
      case 'digest_sent_push':
      case 'engineer_buffer_stats':
      case 'engineer_sent_events':
      case 'engineer_worklog_append':
      case 'engineer_streams':
      case 'engineer_streams_update':
        _markSurface(flags, 'engineer');
        break;
      case 'engineer_settings_update':
        _markSurface(flags, 'main', 'engineer');
        break;
      case 'agent_digest_update':
        _markSurface(flags, 'main', 'engineer');
        break;
      case 'decision_upsert':
      case 'decision_remove':
      case 'pending_hire_upsert':
      case 'pending_hire_resolve':
        _markSurface(flags, 'main', 'engineer');
        break;
      case 'ui_update':
        _applyUiSurfaceInvalidation(flags, op.key);
        break;
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
  if (value === undefined) return '__loom_undefined__';
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
    && task.labels.indexOf('loom:human') >= 0
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
    'provider',
    'external_id',
    'external_url',
    'messages',
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

function _taskDeltaInvalidatesEngineer(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const group = _currentSurfaceGroup();
  if (!_taskTouchesGroup(previous, next, group)) return false;
  const focused = _focusedEngineerAgent();
  if (!focused) return false;
  const kind = _focusedEngineerAgentKind(focused);
  const tab = _focusedEngineerActiveTab(kind);
  if (kind === 'worker') {
    if (tab && tab !== 'worklog') return false;
    const focusedId = String(focused.id || '');
    return !!(
      focusedId
      && ((previous && String(previous.agent_id || '') === focusedId)
        || (next && String(next.agent_id || '') === focusedId))
    );
  }
  if (kind === 'engineer') {
    if (tab && tab !== 'worklog') return false;
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
  if (!_standaloneDeltaOptimizationsEnabled()) {
    _markSurface(flags, 'main', 'board', 'context', 'events', 'engineer');
    return;
  }
  const previous = hint && hint.task ? hint.task : null;
  const next = _taskNextFromDelta(op, previous);
  if (_taskDeltaInvalidatesMain(previous, next, op)) _markSurface(flags, 'main');
  if (_taskDeltaInvalidatesBoard(previous, next, op)) _markSurface(flags, 'board');
  if (_taskDeltaInvalidatesContext(previous, next, op)) _markSurface(flags, 'context');
  if (_taskDeltaInvalidatesEvents(previous, next, op)) _markSurface(flags, 'events');
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
  const agentId = String((op && op.id) || (previous && previous.id) || (next && next.id) || '');
  if (focused && agentId && String(focused.id || '') === agentId) return true;
  return true;
}

function _applyAgentSurfaceInvalidation(flags, op, hint) {
  if (!_standaloneDeltaOptimizationsEnabled()) {
    _markSurface(flags, 'main', 'context', 'events', 'engineer');
    return;
  }
  const previous = hint && hint.agent ? hint.agent : null;
  const next = _agentNextFromDelta(op, previous);
  _markSurface(flags, 'main');
  if (_agentDeltaInvalidatesContext(previous, next, op)) _markSurface(flags, 'context');
  if (_agentDeltaInvalidatesEvents(previous, next, op)) _markSurface(flags, 'events');
  if (_agentDeltaInvalidatesEngineer(previous, next, op)) _markSurface(flags, 'engineer');
}

function _applyUiSurfaceInvalidation(flags, key) {
  if (key === 'standalone_panel_layout') {
    _markSurface(flags, 'board', 'actions', 'context', 'events', 'engineer', 'templates');
  }
  if (key === 'events_dismissed_attention') {
    _markSurface(flags, 'events');
  }
  if (key === 'board_filters_by_group'
      || key === 'board_saved_views_by_group'
      || key === 'board_lane_sorts_by_group'
      || key === 'board_card_density_by_group') {
    _markSurface(flags, 'board');
  }
  if (key === 'selected_principal_id') {
    _markSurface(flags, 'main');
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
        break;
      }
      case 'agent_remove':
        delete state.agents[op.id];
        if (state.agent_digest_settings) delete state.agent_digest_settings[op.id];
        if (state.digest_buffer_stats) delete state.digest_buffer_stats[op.id];
        if (state.digest_sent_events) delete state.digest_sent_events[op.id];
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
        if (state.engineer_session_maps) delete state.engineer_session_maps[op.name];
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
        if (state.engineer_session_maps && state.engineer_session_maps[op.old_name]) {
          state.engineer_session_maps[op.new_name] = state.engineer_session_maps[op.old_name];
          delete state.engineer_session_maps[op.old_name];
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
        if (op.key === 'standalone_panel_layout'
            && typeof _standalonePanelSetLayoutFromState === 'function') {
          _standalonePanelSetLayoutFromState(op.value || {}, { fromServer: true });
          if (typeof _syncVisibleStandalonePanelApps === 'function') {
            _syncVisibleStandalonePanelApps(prevStandaloneVisibleApps);
          }
        }
        if (op.key === 'board_filters_by_group'
            && typeof _boardFiltersByGroup !== 'undefined') {
          _boardFiltersByGroup = null;
          if (typeof _boardFilterStateGroup !== 'undefined') {
            _boardFilterStateGroup = '';
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
        var grp = op.group || '';
        if (grp) {
          if (!state.engineer_journal[grp]) state.engineer_journal[grp] = [];
          var je = Object.assign({}, op);
          delete je.op;
          state.engineer_journal[grp].push(je);
          // Cap at 200 entries per group
          if (state.engineer_journal[grp].length > 200)
            state.engineer_journal[grp] = state.engineer_journal[grp].slice(-200);
        }
        break;
      }

      case 'journal_delete': {
        var grpd = op.group || '';
        if (grpd && state.engineer_journal && state.engineer_journal[grpd]) {
          state.engineer_journal[grpd] = state.engineer_journal[grpd].filter(
            function(e) { return e.id !== op.id; });
        }
        break;
      }

      case 'architect_journal_append': {
        var archId = op.architect_id || '';
        if (archId && state.architect_journals && state.architect_journals[archId]) {
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
    if (cell.session_id !== state.active_session_id) continue;
    selectedTerminalId = id;
    if (cell.cell_type === 'agent') {
      selectedAgentId = id;
      // Architects are rendered as principal cards, not as grid agent cells,
      // so their agent id is not a valid nav item — focusing it would land
      // on the first engineer after the next grid render.
      if ((cell.kind || '') === 'architect') {
        focusedItemId = 'principal:' + (cell.group || '') + ':' + id;
      } else {
        focusedItemId = id;
      }
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
  if (msg.action === 'toggle_broadcast') {
    if (broadcastGroup) {
      closeBroadcast();
    } else if (msg.group) {
      openBroadcast(msg.group);
    }
  } else if (msg.action === 'close_cell') {
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

/* WebSocket connection and shared state */

const WS_URL = `ws://${location.host}/ws`;
let ws = null;
let state = { agents: {}, groups: {}, children: {}, active_session_id: null };
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

function connect() {
  _firstStateReceived = false;
  _resyncPending = false;
  _awaitingFullState = false;
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    document.getElementById('conn-dot').classList.add('ok');
    document.getElementById('conn-dot').title = 'Connected';
  };
  ws.onclose = () => {
    _resyncPending = false;
    _awaitingFullState = false;
    if (typeof _weaverResetSessionMapMeta === 'function') {
      _weaverResetSessionMapMeta({ clearStale: false });
    }
    document.getElementById('conn-dot').classList.remove('ok');
    document.getElementById('conn-dot').title = 'Disconnected';
    setTimeout(connect, 2000);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
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
      if (typeof _boardActDropdownWaiting !== 'undefined' && _boardActDropdownWaiting) {
        _boardShowActionList(msg);
      } else if (typeof _boardBatchActionWaiting !== 'undefined' && _boardBatchActionWaiting) {
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
        // Ignore unsolicited action lists instead of reopening the
        // "Task from Action" modal after reconnect/startup.
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
    } else if (msg.type === 'template_rendered') {
      if (typeof _handleRenderedTemplate === 'function') {
        _handleRenderedTemplate(msg);
      }
    } else if (msg.type === 'action_detail') {
      if ((typeof _panelAppVisible === 'function' && _panelAppVisible('actions'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'actions')) {
        tplEditorReceiveDetail(msg);
      } else {
        _showActionVarForm(msg);
      }
    } else if (msg.type === 'action_rendered') {
      _handleActionRendered(msg);
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
    } else if (msg.type === 'agent_history_list') {
      if (typeof agentHistoryReceiveList === 'function') agentHistoryReceiveList(msg);
    } else if (msg.type === 'agent_history_detail') {
      if (typeof agentHistoryReceiveDetail === 'function') agentHistoryReceiveDetail(msg);
      if (typeof taskHistoryReceiveDetail === 'function') taskHistoryReceiveDetail(msg);
    } else if (msg.type === 'action') {
      handleAction(msg);
    } else if (msg.type === 'weaver_session_map') {
      _handleWeaverSessionMapMessage(msg);
    }
  };
}

function _handleWeaverSessionMapMessage(msg) {
  if (!state.weaver_session_maps) state.weaver_session_maps = {};
  var group = (msg && msg.group) || '';
  if (!group) return;
  state.weaver_session_maps[group] = (msg && msg.session_map) || {};
  if (typeof _weaverReceiveSessionMap === 'function') {
    _weaverReceiveSessionMap(msg);
    return;
  }
  if (((typeof _panelAppVisible === 'function' && _panelAppVisible('weaver'))
      || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'weaver'))
      && typeof renderWeaverPanel === 'function') {
    var currentGroup = (typeof _currentGroup === 'function') ? _currentGroup() : '';
    if (!currentGroup || currentGroup === group) renderWeaverPanel();
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
  if (msg.providers) _cachedProviders = msg.providers;
  if (!state.panel_events) state.panel_events = [];
  if (!state.weaver_buffer_stats) state.weaver_buffer_stats = {};
  if (!state.weaver_sent_events) state.weaver_sent_events = {};
  if (!state.weaver_worklog) state.weaver_worklog = {};
  if (!state.weaver_streams) state.weaver_streams = {};
  if (!state.weaver_session_maps) state.weaver_session_maps = {};
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
  if (!dragInProgress) {
    render();
    if (!shouldRestorePanel && typeof renderActivePanel === 'function') {
      renderActivePanel();
    }
  }
  if (typeof _weaverResetSessionMapMeta === 'function') {
    _weaverResetSessionMapMeta({ refetchOpenMissing: true });
  }
  // Restore board panel state on first load
  if (typeof _restorePanelState === 'function') _restorePanelState();
}

/* -- Delta patching ------------------------------------------------------- */

function _handleDelta(msg) {
  if (_awaitingFullState) return;
  const prevGroup = (typeof _currentGroup === 'function') ? _currentGroup() : '';
  const invalidations = _deltaSurfaceInvalidations(msg.ops);
  const opGroupHints = _captureDeltaGroupHints(msg.ops);
  if (msg.seq !== _expectedSeq) {
    // Sequence gap — request full resync
    if (!_resyncPending) {
      _resyncPending = true;
      _awaitingFullState = true;
      send({ cmd: 'resync' });
    }
    return;
  }
  _expectedSeq = msg.seq + 1;
  _applyDelta(msg.ops);
  const sessionMapGroups = _collectSessionMapInvalidationGroups(msg.ops, opGroupHints);
  if (sessionMapGroups.length && typeof _weaverMarkSessionMapStale === 'function') {
    _weaverMarkSessionMapStale(sessionMapGroups);
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
    if (typeof renderInvalidatedSurfaces === 'function') {
      renderInvalidatedSurfaces(invalidations);
    } else {
      render();
    }
  }
}

function _blankSurfaceInvalidations() {
  return {
    main: false,
    board: false,
    context: false,
    events: false,
    weaver: false,
    templates: false,
  };
}

function _markSurface(flags) {
  for (let i = 1; i < arguments.length; i++) {
    flags[arguments[i]] = true;
  }
}

function _deltaSurfaceInvalidations(ops) {
  const flags = _blankSurfaceInvalidations();
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i];
    switch (op.op) {
      case 'agent_upsert':
      case 'agent_remove':
        _markSurface(flags, 'main', 'context', 'events', 'weaver');
        break;
      case 'group_update':
      case 'group_remove':
      case 'group_rename':
      case 'groups_reorder':
      case 'group_settings_update':
      case 'focus_update':
      case 'global_settings_update':
        _markSurface(flags, 'main', 'context', 'weaver');
        break;
      case 'task_upsert':
      case 'task_remove':
        _markSurface(flags, 'main', 'board', 'context', 'events', 'weaver');
        break;
      case 'lanes_update':
      case 'schedule_upsert':
      case 'schedule_remove':
        _markSurface(flags, 'board');
        break;
      case 'event_append':
        _markSurface(flags, 'events');
        break;
      case 'journal_append':
      case 'journal_delete':
      case 'weaver_buffer_stats':
      case 'weaver_sent_events':
      case 'weaver_worklog_append':
      case 'weaver_streams':
      case 'weaver_streams_update':
        _markSurface(flags, 'weaver');
        break;
      case 'weaver_settings_update':
        _markSurface(flags, 'main', 'weaver');
        break;
      case 'decision_upsert':
      case 'decision_remove':
      case 'pending_hire_upsert':
      case 'pending_hire_resolve':
        _markSurface(flags, 'main');
        break;
      case 'ui_update':
        _applyUiSurfaceInvalidation(flags, op.key);
        break;
    }
  }
  return flags;
}

function _applyUiSurfaceInvalidation(flags, key) {
  if (key === 'standalone_panel_layout') {
    _markSurface(flags, 'board', 'actions', 'context', 'events', 'weaver', 'templates');
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
      case 'weaver_settings_update':
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
  return surface === 'context' || surface === 'weaver';
}

function _captureDeltaGroupHints(ops) {
  const hints = [];
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i] || {};
    let group = '';
    if (op.op === 'agent_remove' && state && state.agents && state.agents[op.id]) {
      group = state.agents[op.id].group || '';
    } else if (op.op === 'task_remove'
        && state && state.board_tasks && state.board_tasks[op.id]) {
      group = state.board_tasks[op.id].group || '';
    }
    hints.push({ group: group });
  }
  return hints;
}

function _opTouchesGroup(op, group, hint) {
  if (!op || !group) return true;
  const hintedGroup = (hint && hint.group) ? hint.group : '';
  switch (op.op) {
    case 'agent_upsert':
    case 'task_upsert':
    case 'event_append':
      return (op.group || hintedGroup || '') === group;
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
    case 'weaver_buffer_stats':
    case 'weaver_sent_events':
    case 'weaver_worklog_append':
    case 'weaver_streams':
    case 'weaver_streams_update':
    case 'weaver_settings_update':
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
        if (state.agents[id]) {
          Object.assign(state.agents[id], op);
        } else {
          state.agents[id] = Object.assign({}, op);
        }
        // Clean the 'op' key from the agent data
        delete state.agents[id].op;
        break;
      }
      case 'agent_remove':
        delete state.agents[op.id];
        // Selection globals (`selectedAgentId` / `selectedTerminalId` /
        // `focusedItemId`) are browser-local — the server doesn't know
        // about them. When the agent they reference gets removed
        // (cascade, `loom ai ready`, group delete, etc.), clear the
        // stale id here so the detail panel + terminal drawer don't
        // keep rendering the ghost cell.
        if (typeof selectedAgentId !== 'undefined' && selectedAgentId === op.id) {
          selectedAgentId = null;
        }
        if (typeof selectedTerminalId !== 'undefined' && selectedTerminalId === op.id) {
          selectedTerminalId = null;
        }
        if (typeof focusedItemId !== 'undefined' && focusedItemId === op.id) {
          focusedItemId = null;
        }
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
        if (state.weaver_buffer_stats) delete state.weaver_buffer_stats[op.name];
        if (state.weaver_sent_events) delete state.weaver_sent_events[op.name];
        if (state.weaver_worklog) delete state.weaver_worklog[op.name];
        if (state.weaver_streams) delete state.weaver_streams[op.name];
        if (state.weaver_session_maps) delete state.weaver_session_maps[op.name];
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
        if (state.weaver_buffer_stats && state.weaver_buffer_stats[op.old_name]) {
          state.weaver_buffer_stats[op.new_name] = state.weaver_buffer_stats[op.old_name];
          delete state.weaver_buffer_stats[op.old_name];
        }
        if (state.weaver_sent_events && state.weaver_sent_events[op.old_name]) {
          state.weaver_sent_events[op.new_name] = state.weaver_sent_events[op.old_name];
          delete state.weaver_sent_events[op.old_name];
        }
        if (state.weaver_worklog && state.weaver_worklog[op.old_name]) {
          state.weaver_worklog[op.new_name] = state.weaver_worklog[op.old_name];
          delete state.weaver_worklog[op.old_name];
        }
        if (state.weaver_streams && state.weaver_streams[op.old_name]) {
          state.weaver_streams[op.new_name] = state.weaver_streams[op.old_name];
          delete state.weaver_streams[op.old_name];
        }
        if (state.weaver_session_maps && state.weaver_session_maps[op.old_name]) {
          state.weaver_session_maps[op.new_name] = state.weaver_session_maps[op.old_name];
          delete state.weaver_session_maps[op.old_name];
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
        _maybeTriggerAgentDoneFlourish(previousTask, state.board_tasks[id]);
        break;
      }
      case 'task_remove':
        if (state.board_tasks) delete state.board_tasks[op.id];
        break;

      case 'lanes_update':
        state.board_lanes = op.lanes;
        break;

      case 'global_settings_update': {
        const gs = Object.assign({}, op);
        delete gs.op;
        state.global_settings = gs;
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
        if (!state.weaver_journal) state.weaver_journal = {};
        var grp = op.group || '';
        if (grp) {
          if (!state.weaver_journal[grp]) state.weaver_journal[grp] = [];
          var je = Object.assign({}, op);
          delete je.op;
          state.weaver_journal[grp].push(je);
          // Cap at 200 entries per group
          if (state.weaver_journal[grp].length > 200)
            state.weaver_journal[grp] = state.weaver_journal[grp].slice(-200);
        }
        break;
      }

      case 'journal_delete': {
        var grpd = op.group || '';
        if (grpd && state.weaver_journal && state.weaver_journal[grpd]) {
          state.weaver_journal[grpd] = state.weaver_journal[grpd].filter(
            function(e) { return e.id !== op.id; });
        }
        break;
      }

      case 'weaver_buffer_stats': {
        if (!state.weaver_buffer_stats) state.weaver_buffer_stats = {};
        var bsg = op.group || '';
        if (bsg) {
          state.weaver_buffer_stats[bsg] = {
            buffered_events: op.buffered_events || 0,
            next_push_in: op.next_push_in || 0,
            next_push_at: op.next_push_at || 0,
            queued_events: op.queued_events || [],
            manual_flush_requested: !!op.manual_flush_requested,
          };
        }
        break;
      }

      case 'weaver_sent_events': {
        if (!state.weaver_sent_events) state.weaver_sent_events = {};
        var wsg = op.group || '';
        if (wsg) {
          state.weaver_sent_events[wsg] = op.events || [];
        }
        break;
      }

      case 'weaver_worklog_append': {
        if (!state.weaver_worklog) state.weaver_worklog = {};
        var wlg = op.group || '';
        if (wlg) {
          if (!state.weaver_worklog[wlg]) state.weaver_worklog[wlg] = [];
          var worklogEntry = Object.assign({}, op.entry || {});
          state.weaver_worklog[wlg].unshift(worklogEntry);
          if (state.weaver_worklog[wlg].length > 200) {
            state.weaver_worklog[wlg] = state.weaver_worklog[wlg].slice(0, 200);
          }
        }
        break;
      }

      case 'weaver_streams':
      case 'weaver_streams_update': {
        if (!state.weaver_streams) state.weaver_streams = {};
        var wstg = op.group || '';
        if (wstg) {
          if (Object.prototype.hasOwnProperty.call(op, 'streams')) {
            state.weaver_streams[wstg] = op.streams;
          } else if (Array.isArray(op.items)) {
            state.weaver_streams[wstg] = { items: op.items };
          } else {
            state.weaver_streams[wstg] = [];
          }
        }
        break;
      }

      case 'weaver_settings_update': {
        if (!state.weaver_settings) state.weaver_settings = {};
        var wg = op.group || '';
        if (wg) {
          var ws = Object.assign({}, op);
          delete ws.op;
          state.weaver_settings[wg] = ws;
        }
        break;
      }

      case 'decision_upsert': {
        if (!state.decisions) state.decisions = {};
        var decisionId = op.id;
        if (decisionId) {
          var decision = Object.assign({}, op);
          delete decision.op;
          state.decisions[decisionId] = decision;
        }
        break;
      }

      case 'decision_remove':
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
  } else if (msg.action === 'add_terminal') {
    if (msg.group && msg.parent_id) quickAddTerminal(msg.group, msg.parent_id);
  }
}

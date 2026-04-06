/* WebSocket connection and shared state */

const WS_URL = `ws://${location.host}/ws`;
let ws = null;
let state = { agents: {}, groups: {}, children: {}, active_session_id: null };
let dragInProgress = false;
let selectedAgentId = null;
let focusedItemId = null;
let _cachedAgentTemplates = [];

var _firstStateReceived = false;
var _expectedSeq = 0;

function connect() {
  _firstStateReceived = false;
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    document.getElementById('conn-dot').classList.add('ok');
    document.getElementById('conn-dot').title = 'Connected';
  };
  ws.onclose = () => {
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
      if (msg.templates) _cachedAgentTemplates = msg.templates;
      if (_pendingModal) {
        _showAddModal(_pendingModal.mode, _pendingModal.group, msg);
        _pendingModal = null;
      }
    } else if (msg.type === 'group_settings') {
      if (msg.providers) _cachedProviders = msg.providers;
      if (msg.templates) _cachedAgentTemplates = msg.templates;
      _showGroupSettings(msg.group, msg);
    } else if (msg.type === 'toast') {
      _showToast(msg.message, msg.level);
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
      if (typeof _boardActDropdownWaiting !== 'undefined' && _boardActDropdownWaiting) {
        _boardShowActionList(msg);
      } else if (typeof _schedModalWaiting !== 'undefined' && _schedModalWaiting) {
        _handleScheduleActionList(msg);
      } else if (typeof _taskModalWaiting !== 'undefined' && _taskModalWaiting) {
        _handleTaskActionList(msg);
      } else if (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'actions') {
        tplEditorReceiveList(msg);
      } else {
        _showActionList(msg);
      }
    } else if (msg.type === 'templates') {
      _cachedAgentTemplates = msg.templates || [];
      if (typeof _taskTemplateWaiting !== 'undefined' && _taskTemplateWaiting) {
        _handleTaskTemplateList(msg);
      } else if (typeof _activePanelApp !== 'undefined'
          && _activePanelApp === 'templates'
          && typeof agentTemplateReceiveList === 'function') {
        agentTemplateReceiveList(msg);
      } else if (typeof _activePanelApp !== 'undefined'
          && _activePanelApp === 'actions'
          && typeof renderTemplatesEditor === 'function') {
        renderTemplatesEditor();
      }
    } else if (msg.type === 'template_detail') {
      if (typeof _activePanelApp !== 'undefined'
          && _activePanelApp === 'templates'
          && typeof agentTemplateReceiveDetail === 'function') {
        agentTemplateReceiveDetail(msg);
      }
    } else if (msg.type === 'template_rendered') {
      if (typeof _handleRenderedTemplate === 'function') {
        _handleRenderedTemplate(msg);
      }
    } else if (msg.type === 'action_detail') {
      if (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'actions') {
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
    } else if (msg.type === 'pipelines') {
      if (typeof tplReceivePipelines !== 'undefined') tplReceivePipelines(msg);
    } else if (msg.type === 'global_settings') {
      _showGlobalSettingsModal(msg);
    } else if (msg.type === 'events_page') {
      if (typeof handleEventsPage === 'function') handleEventsPage(msg);
    } else if (msg.type === 'agent_history_list') {
      if (typeof agentHistoryReceiveList === 'function') agentHistoryReceiveList(msg);
    } else if (msg.type === 'agent_history_detail') {
      if (typeof agentHistoryReceiveDetail === 'function') agentHistoryReceiveDetail(msg);
    } else if (msg.type === 'action') {
      handleAction(msg);
    }
  };
}

/* -- Full state (initial connect + resync) -------------------------------- */

function _handleFullState(msg) {
  const prevActive = state.active_session_id;
  state = msg;
  if (!state.panel_events) state.panel_events = [];
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
  if (!dragInProgress) render();
  // Restore board panel state on first load
  if (typeof _restorePanelState === 'function') _restorePanelState();
}

/* -- Delta patching ------------------------------------------------------- */

function _handleDelta(msg) {
  if (msg.seq !== _expectedSeq) {
    // Sequence gap — request full resync
    send({ cmd: 'resync' });
    return;
  }
  _expectedSeq = msg.seq + 1;
  _applyDelta(msg.ops);
  if (!dragInProgress) render();
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
        break;

      case 'group_update':
        state.groups[op.name] = op.agents;
        break;
      case 'group_remove':
        delete state.groups[op.name];
        delete state.group_settings[op.name];
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
        if (state.board_tasks[id]) {
          Object.assign(state.board_tasks[id], op);
        } else {
          state.board_tasks[id] = Object.assign({}, op);
        }
        delete state.board_tasks[id].op;
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
        state[op.key] = op.value;
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
    if (cell.cell_type === 'agent') {
      selectedAgentId = id;
      focusedItemId = id;
    } else if (cell.parent_id) {
      selectedAgentId = cell.parent_id;
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
  } else if (msg.action === 'add_terminal') {
    if (msg.group && msg.parent_id) quickAddTerminal(msg.group, msg.parent_id);
  }
}

/* WebSocket connection and shared state */

const WS_URL = `ws://${location.host}/ws`;
let ws = null;
let state = { agents: {}, groups: {}, children: {}, active_session_id: null };
let dragInProgress = false;
let selectedAgentId = null;
let focusedItemId = null;

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
      if (_pendingModal) {
        _showAddModal(_pendingModal.mode, _pendingModal.group, msg);
        _pendingModal = null;
      }
    } else if (msg.type === 'group_settings') {
      _showGroupSettings(msg.group, msg);
    } else if (msg.type === 'toast') {
      _showToast(msg.message, msg.level);
    } else if (msg.type === 'worktree_history') {
      _showWorktreeHistory(msg);
    } else if (msg.type === 'templates') {
      if (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'templates') {
        tplEditorReceiveList(msg);
      } else {
        _showTemplateList(msg);
      }
    } else if (msg.type === 'template_detail') {
      if (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'templates') {
        tplEditorReceiveDetail(msg);
      } else {
        _showTemplateVarForm(msg);
      }
    } else if (msg.type === 'template_rendered') {
      _handleTemplateRendered(msg);
    } else if (msg.type === 'global_settings') {
      _showGlobalSettingsModal(msg);
    } else if (msg.type === 'action') {
      handleAction(msg);
    }
  };
}

/* -- Full state (initial connect + resync) -------------------------------- */

function _handleFullState(msg) {
  const prevActive = state.active_session_id;
  state = msg;
  _expectedSeq = (msg.seq || 0) + 1;
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

      case 'ui_update':
        state[op.key] = op.value;
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
  }
}

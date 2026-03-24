/* WebSocket connection and shared state */

const WS_URL = `ws://${location.host}/ws`;
let ws = null;
let state = { agents: {}, groups: {}, children: {}, active_session_id: null };
let dragInProgress = false;
let selectedAgentId = null;
let focusedItemId = null;

function connect() {
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
      const prevActive = state.active_session_id;
      state = msg;
      // When user switches to a managed tab, select its agent
      if (state.active_session_id && state.active_session_id !== prevActive) {
        _syncSelectionToActiveSession();
      }
      if (!dragInProgress) render();
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
    } else if (msg.type === 'action') {
      handleAction(msg);
    }
  };
}

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

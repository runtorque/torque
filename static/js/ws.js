/* WebSocket connection and shared state */

const WS_URL = `ws://${location.host}/ws`;
let ws = null;
let state = { agents: {}, groups: {}, active_session_id: null };
let dragInProgress = false;

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
      state = msg;
      if (!dragInProgress) render();
    } else if (msg.type === 'config') {
      if (_pendingModal) {
        _showAddModal(_pendingModal.mode, _pendingModal.group, msg);
        _pendingModal = null;
      }
    }
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

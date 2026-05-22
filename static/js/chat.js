/* Read-only aggregate Chat panel */

var _chatSelectedThreadId = '';

function renderChatPanel() {
  var root = document.getElementById('panel-chat');
  if (!root) return;
  root.innerHTML = '<div class="chat-panel chat-panel-empty">Chat panel loading…</div>';
}

/* Task history modal for an agent */

var _taskHistoryOpen = false;
var _taskHistoryAgentId = '';
var _taskHistoryData = null;
var _taskHistoryExpandedTask = '';

function showTaskHistory(agentId) {
  if (!agentId) return;
  _taskHistoryOpen = true;
  _taskHistoryAgentId = agentId;
  _taskHistoryData = null;
  _taskHistoryExpandedTask = '';
  renderTaskHistory();
  send({ cmd: 'get_agent_history_detail', agent_id: agentId });
}

function hideTaskHistory() {
  _taskHistoryOpen = false;
  _taskHistoryAgentId = '';
  _taskHistoryData = null;
  _taskHistoryExpandedTask = '';
  renderTaskHistory();
}

function taskHistoryReceiveDetail(msg) {
  if (!_taskHistoryOpen || !msg.record
      || msg.record.id !== _taskHistoryAgentId) return;
  _taskHistoryData = msg;
  renderTaskHistory();
}

function toggleTaskHistoryItem(taskId) {
  _taskHistoryExpandedTask = _taskHistoryExpandedTask === taskId ? '' : taskId;
  renderTaskHistory();
}

function _thRelTime(ts) {
  if (!ts) return '\u2014';
  var d = new Date(ts * 1000);
  var diff = Date.now() - d.getTime();
  if (diff < 60000) return 'just now';
  if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
  if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
  if (diff < 604800000) return Math.floor(diff / 86400000) + 'd ago';
  return d.toLocaleDateString();
}

function _thActionIcon(action) {
  var icons = {
    done: '\u2713', ready: '\u2713', blocked: '\u26A0',
    error: '\u2717', progress: '\u25B6', derive: '\u2192',
    ask: '\u2753', name: '\u270E',
    weaver_message: '\u2709', reply: '\u21A9'
  };
  return icons[action] || '\u2022';
}

function _thOutcomeBadge(outcome) {
  var o = outcome || 'in-progress';
  var cls = 'th-outcome-' + o.replace(/[^a-z]/g, '');
  return '<span class="th-outcome ' + cls + '">' + esc(o) + '</span>';
}

function _thGetTaskMessages(taskId) {
  // Prefer board task messages (richer, include agent_name)
  var bt = (state.board_tasks || {})[taskId];
  if (bt && bt.messages && bt.messages.length) {
    return bt.messages.slice().sort(function(a, b) {
      return (b.timestamp || 0) - (a.timestamp || 0);
    });
  }
  // Fall back to agent_messages filtered by task_id
  if (!_taskHistoryData) return [];
  var msgs = _taskHistoryData.messages || [];
  return msgs.filter(function(m) { return m.task_id === taskId; });
}

function _thRenderMessages(taskId) {
  var messages = _thGetTaskMessages(taskId);
  if (!messages.length) {
    return '<div class="th-no-msgs">No messages recorded.</div>';
  }
  var html = '';
  for (var i = 0; i < messages.length; i++) {
    var m = messages[i];
    var icon = _thActionIcon(m.action);
    html += '<div class="th-msg-row th-msg-' + esc(m.action) + '">';
    html += '<span class="th-msg-icon">' + icon + '</span>';
    html += '<span class="th-msg-action">' + esc(m.action) + '</span>';
    html += '<span class="th-msg-text">' + esc(m.message || '') + '</span>';
    html += '<span class="th-msg-time">' + _thRelTime(m.timestamp) + '</span>';
    html += '</div>';
  }
  return html;
}

function renderTaskHistory() {
  var overlay = document.getElementById('modal-task-history');
  var root = document.getElementById('task-history-root');
  document.body.classList.remove('task-history-open');
  if (overlay) overlay.classList.toggle('visible', _taskHistoryOpen);
  if (!root) return;

  if (!_taskHistoryOpen) {
    root.innerHTML = '';
    return;
  }

  var html = '<div class="th-view">';

  // Loading state
  if (!_taskHistoryData) {
    html += '<div class="th-header">';
    html += '<div class="th-title">Loading\u2026</div>';
    html += '<button class="th-close" onclick="hideTaskHistory()">\u2715</button>';
    html += '</div>';
    html += '<div class="th-content"><div class="th-empty">Loading task history\u2026</div></div>';
    html += '<div class="th-footer">';
    html += '<button class="btn-cancel" onclick="hideTaskHistory()">Close</button>';
    html += '</div>';
    html += '</div>';
    root.innerHTML = html;
    return;
  }

  var agent = _taskHistoryData.record || {};
  var tasks = _taskHistoryData.tasks || [];
  var agentName = agent.name || 'Agent';

  // Also check live state for name
  var liveCell = state.agents && state.agents[_taskHistoryAgentId];
  if (liveCell) agentName = liveCell.name;

  // Header
  html += '<div class="th-header">';
  html += '<div class="th-title">' + esc(agentName) + '</div>';
  html += '<div class="th-subtitle">'
    + tasks.length + ' task' + (tasks.length !== 1 ? 's' : '') + '</div>';
  html += '<button class="th-close" onclick="hideTaskHistory()">\u2715</button>';
  html += '</div>';

  // Content
  html += '<div class="th-content">';
  if (!tasks.length) {
    html += '<div class="th-empty">No tasks recorded for this agent.</div>';
  } else {
    for (var i = 0; i < tasks.length; i++) {
      var t = tasks[i];
      var expanded = _taskHistoryExpandedTask === t.task_id;
      var arrow = expanded ? '\u25BC' : '\u25B6';

      html += '<div class="th-task' + (expanded ? ' expanded' : '') + '">';
      html += '<div class="th-task-row" onclick="toggleTaskHistoryItem(\''
        + esc(t.task_id) + '\')">';
      html += '<span class="th-expand">' + arrow + '</span>';
      html += _thOutcomeBadge(t.outcome);
      html += '<span class="th-task-title">' + esc(t.task_title) + '</span>';
      html += '<span class="th-task-time">' + _thRelTime(t.started_at);
      if (t.completed_at) {
        html += ' \u2192 ' + _thRelTime(t.completed_at);
      }
      html += '</span>';
      html += '</div>';

      if (expanded) {
        html += '<div class="th-task-expanded">';
        html += _thRenderMessages(t.task_id);
        html += '</div>';
      }

      html += '</div>';
    }
  }
  html += '</div>';

  // Footer
  html += '<div class="th-footer">';
  html += '<button class="btn-cancel" onclick="hideTaskHistory()">Close</button>';
  html += '</div>';

  html += '</div>';
  root.innerHTML = html;
}

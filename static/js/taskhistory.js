/* Task history modal for an agent */

var _taskHistoryOpen = false;
var _taskHistoryAgentId = '';
var _taskHistoryData = null;
var _taskHistoryExpandedTask = '';

var _taskHistoryScrollTop = 0;
var _taskHistoryViewportHeight = 0;
var _taskHistoryScrollFrame = 0;
var _TASK_HISTORY_ROW_HEIGHT = 40;
var _TASK_HISTORY_EXPANDED_ROW_EXTRA = 96;
var _TASK_HISTORY_OVERSCAN = 8;
var _TASK_HISTORY_VIRTUAL_THRESHOLD = 80;
var _TASK_HISTORY_DEFAULT_VIEWPORT = 640;

function _thJsString(value) {
  return JSON.stringify(String(value == null ? '' : value));
}

function _thAttr(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _thJsAttrString(value) {
  return _thAttr(_thJsString(value));
}

function _thEstimatedRowHeight(task) {
  var height = _TASK_HISTORY_ROW_HEIGHT;
  if (task && _taskHistoryExpandedTask === task.task_id) {
    var messageCount = _thGetTaskMessages(task.task_id).length;
    height += _TASK_HISTORY_EXPANDED_ROW_EXTRA + (Math.max(0, messageCount - 1) * 22);
  }
  return height;
}

function _thEstimatedHeight(tasks, start, end) {
  var height = 0;
  tasks = tasks || [];
  start = Math.max(0, Number(start) || 0);
  end = Math.min(tasks.length, Math.max(start, Number(end) || 0));
  for (var i = start; i < end; i++) {
    height += _thEstimatedRowHeight(tasks[i]);
  }
  return height;
}

function _thVirtualRange(tasks) {
  tasks = tasks || [];
  var total = tasks.length;
  if (total <= _TASK_HISTORY_VIRTUAL_THRESHOLD) {
    return {
      start: 0,
      end: total,
      before: 0,
      after: 0,
      virtualized: false,
    };
  }
  var scrollTop = Math.max(0, Number(_taskHistoryScrollTop || 0));
  var viewport = Math.max(
    120,
    Number(_taskHistoryViewportHeight || 0) || _TASK_HISTORY_DEFAULT_VIEWPORT
  );
  var start = 0;
  var y = 0;
  while (start < total) {
    var nextY = y + _thEstimatedRowHeight(tasks[start]);
    if (nextY >= scrollTop) break;
    y = nextY;
    start++;
  }
  start = Math.max(0, start - _TASK_HISTORY_OVERSCAN);
  var before = _thEstimatedHeight(tasks, 0, start);
  var end = start;
  var renderedHeight = 0;
  var targetHeight = viewport + (_TASK_HISTORY_OVERSCAN * _TASK_HISTORY_ROW_HEIGHT * 2);
  while (end < total && renderedHeight < targetHeight) {
    renderedHeight += _thEstimatedRowHeight(tasks[end]);
    end++;
  }
  end = Math.min(total, Math.max(end, start + 1));
  return {
    start: start,
    end: end,
    before: before,
    after: _thEstimatedHeight(tasks, end, total),
    virtualized: true,
  };
}

function _thSpacer(height) {
  height = Math.max(0, Math.round(Number(height) || 0));
  if (!height) return '';
  return '<div class="th-virtual-spacer" aria-hidden="true" style="height:'
    + height + 'px"></div>';
}

function _thAnchorItems(container) {
  if (!container || typeof container.querySelectorAll !== 'function') return [];
  var items = container.querySelectorAll('[data-th-anchor]') || [];
  var out = [];
  for (var i = 0; i < items.length; i++) out.push(items[i]);
  return out;
}

function _thAnchorKey(item) {
  if (!item) return '';
  if (item.dataset && item.dataset.thAnchor) return String(item.dataset.thAnchor);
  if (typeof item.getAttribute === 'function') {
    return String(item.getAttribute('data-th-anchor') || '');
  }
  return '';
}

function _thCaptureScrollAnchor(container) {
  if (!container || typeof container.getBoundingClientRect !== 'function') return null;
  if (typeof container.scrollTop === 'number') _taskHistoryScrollTop = container.scrollTop;
  if (typeof container.clientHeight === 'number' && container.clientHeight > 0) {
    _taskHistoryViewportHeight = container.clientHeight;
  }
  var items = _thAnchorItems(container);
  if (!items.length) return null;
  var containerRect = container.getBoundingClientRect();
  var best = null;
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    if (!item || typeof item.getBoundingClientRect !== 'function') continue;
    var rect = item.getBoundingClientRect();
    if (rect.bottom >= containerRect.top) {
      best = item;
      break;
    }
  }
  if (!best) best = items[0];
  if (!best || typeof best.getBoundingClientRect !== 'function') return null;
  var anchorRect = best.getBoundingClientRect();
  return {
    key: _thAnchorKey(best),
    offset: anchorRect.top - containerRect.top,
  };
}

function _thRestoreScrollAnchor(container, snapshot) {
  if (!container || typeof container.scrollTop !== 'number') return;
  container.scrollTop = _taskHistoryScrollTop;
  if (!snapshot || !snapshot.key || typeof container.getBoundingClientRect !== 'function') return;
  var items = _thAnchorItems(container);
  var target = null;
  for (var i = 0; i < items.length; i++) {
    if (_thAnchorKey(items[i]) === snapshot.key) {
      target = items[i];
      break;
    }
  }
  if (!target || typeof target.getBoundingClientRect !== 'function') return;
  var containerRect = container.getBoundingClientRect();
  var targetRect = target.getBoundingClientRect();
  container.scrollTop += (targetRect.top - containerRect.top) - (snapshot.offset || 0);
  _taskHistoryScrollTop = container.scrollTop;
}

function _thContentElement(root) {
  if (!root || typeof root.querySelector !== 'function') return null;
  return root.querySelector('.th-content');
}

function _thScheduleScrollRender(container) {
  if (container && typeof container.scrollTop === 'number') {
    _taskHistoryScrollTop = container.scrollTop;
  }
  if (container && typeof container.clientHeight === 'number' && container.clientHeight > 0) {
    _taskHistoryViewportHeight = container.clientHeight;
  }
  if (_taskHistoryScrollFrame) return;
  var scheduler = typeof requestAnimationFrame === 'function'
    ? requestAnimationFrame
    : function(fn) { return setTimeout(fn, 0); };
  _taskHistoryScrollFrame = scheduler(function() {
    _taskHistoryScrollFrame = 0;
    if (_taskHistoryOpen) renderTaskHistory();
  });
}

function _thAttachScroll(root) {
  var content = _thContentElement(root);
  if (!content || typeof content.addEventListener !== 'function') return;
  content.scrollTop = _taskHistoryScrollTop;
  if (typeof content.clientHeight === 'number' && content.clientHeight > 0) {
    _taskHistoryViewportHeight = content.clientHeight;
  }
  content.addEventListener('scroll', function(evt) {
    _thScheduleScrollRender((evt && (evt.currentTarget || evt.target)) || content);
  });
}

function showTaskHistory(agentId) {
  if (!agentId) return;
  _taskHistoryOpen = true;
  _taskHistoryAgentId = agentId;
  _taskHistoryData = null;
  _taskHistoryExpandedTask = '';
  _taskHistoryScrollTop = 0;
  _taskHistoryViewportHeight = 0;
  renderTaskHistory();
  send({ cmd: 'get_agent_history_detail', agent_id: agentId });
}

function hideTaskHistory() {
  _taskHistoryOpen = false;
  _taskHistoryAgentId = '';
  _taskHistoryData = null;
  _taskHistoryExpandedTask = '';
  _taskHistoryScrollTop = 0;
  _taskHistoryViewportHeight = 0;
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
  if (_taskHistoryExpandedTask && typeof ensureTaskDetail === 'function') {
    ensureTaskDetail(_taskHistoryExpandedTask, function() {
      if (_taskHistoryOpen) renderTaskHistory();
    });
  }
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
  var previousContent = root ? _thContentElement(root) : null;
  var scrollAnchor = previousContent ? _thCaptureScrollAnchor(previousContent) : null;
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
    _thAttachScroll(root);
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
    var range = _thVirtualRange(tasks);
    html += '<div class="th-virtual-list" data-th-virtualized="'
      + (range.virtualized ? 'true' : 'false') + '">';
    html += _thSpacer(range.before);
    for (var i = range.start; i < range.end; i++) {
      var t = tasks[i];
      var expanded = _taskHistoryExpandedTask === t.task_id;
      var arrow = expanded ? '\u25BC' : '\u25B6';

      html += '<div class="th-task' + (expanded ? ' expanded' : '')
        + '" data-th-anchor="task-' + esc(t.task_id) + '">';
      html += '<div class="th-task-row" onclick="toggleTaskHistoryItem('
        + _thJsAttrString(t.task_id) + ')">';
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
    html += _thSpacer(range.after);
    html += '</div>';
  }
  html += '</div>';

  // Footer
  html += '<div class="th-footer">';
  html += '<button class="btn-cancel" onclick="hideTaskHistory()">Close</button>';
  html += '</div>';

  html += '</div>';
  root.innerHTML = html;
  var nextContent = _thContentElement(root);
  _thRestoreScrollAnchor(nextContent, scrollAnchor);
  _thAttachScroll(root);
}

/* Read-only aggregate Chat panel */

var _chatSelectedThreadId = '';
var _chatExpandedContextById = {};
var _chatVisibleMessageCountByThread = {};

var _CHAT_RESPONSIVE_NARROW_MAX_WIDTH = 640;
var _CHAT_MESSAGE_WINDOW_SIZE = 20;
var _CHAT_MESSAGE_SCROLL_TOP_THRESHOLD = 36;

function _chatResponsiveEntryWidth(entry) {
  if (!entry) return 0;
  if (entry.contentRect && Number.isFinite(Number(entry.contentRect.width))) {
    return Number(entry.contentRect.width);
  }
  var borderBox = entry.borderBoxSize;
  if (Array.isArray(borderBox)) borderBox = borderBox[0];
  if (borderBox && Number.isFinite(Number(borderBox.inlineSize))) {
    return Number(borderBox.inlineSize);
  }
  return 0;
}

function _chatMeasuredPanelWidth(shell, root) {
  var candidates = [];
  if (root) candidates.push(root);
  if (shell) candidates.push(shell);
  for (var i = 0; i < candidates.length; i++) {
    var el = candidates[i];
    if (!el) continue;
    if (typeof el.getBoundingClientRect === 'function') {
      var rect = el.getBoundingClientRect();
      var rectWidth = rect && Number(rect.width || 0);
      if (Number.isFinite(rectWidth) && rectWidth > 0) return rectWidth;
    }
    var clientWidth = Number(el.clientWidth || 0);
    if (Number.isFinite(clientWidth) && clientWidth > 0) return clientWidth;
    var offsetWidth = Number(el.offsetWidth || 0);
    if (Number.isFinite(offsetWidth) && offsetWidth > 0) return offsetWidth;
  }
  return 0;
}

function _chatScrollAtTail(container) {
  if (!container || typeof container.scrollTop !== 'number') return false;
  var scrollTop = Number(container.scrollTop || 0);
  var clientHeight = Number(container.clientHeight || 0);
  var scrollHeight = Number(container.scrollHeight || 0);
  return scrollHeight <= clientHeight || (scrollHeight > 0 && scrollTop + clientHeight >= scrollHeight - 12);
}

function _chatCurrentRenderedThreadId(shell) {
  var root = shell && (shell._chatResponsiveRoot || shell.parentNode);
  if (!root || !root.getAttribute) return '';
  return String(root.getAttribute('data-chat-rendered-thread-id') || '');
}

function _chatUpdateMessageTailState(shell) {
  if (!shell) return false;
  var messages = _chatShellPart(shell, '[data-chat-message-list]', 'messages');
  var atTail = _chatScrollAtTail(messages);
  shell._chatMessagesPinnedToTail = atTail;
  shell._chatMessagesPinnedThreadId = _chatCurrentRenderedThreadId(shell);
  return atTail;
}

function _chatStoredMessageTailPinned(shell) {
  if (!shell || !shell._chatMessagesPinnedToTail) return false;
  var threadId = _chatCurrentRenderedThreadId(shell);
  return !threadId || !shell._chatMessagesPinnedThreadId || shell._chatMessagesPinnedThreadId === threadId;
}

function _chatAttachMessageTailTracker(shell) {
  if (!shell) return;
  var messages = _chatShellPart(shell, '[data-chat-message-list]', 'messages');
  if (!messages || messages._chatTailTrackerShell === shell) return;
  messages._chatTailTrackerShell = shell;
  if (typeof messages.addEventListener === 'function') {
    messages.addEventListener('scroll', function() {
      _chatUpdateMessageTailState(shell);
      _chatLoadOlderMessagesIfNeeded(shell);
    });
  }
}

function _chatCaptureLayoutScroll(shell) {
  if (!shell) return null;
  var list = _chatShellPart(shell, '[data-chat-thread-list]', 'list');
  var messages = _chatShellPart(shell, '[data-chat-message-list]', 'messages');
  var messageCapture = _chatCaptureScrollAnchor(messages, 'data-chat-message-id');
  return {
    thread: _chatCaptureScrollAnchor(list, 'data-chat-thread-id'),
    messages: messageCapture,
    messagesPinTail: _chatStoredMessageTailPinned(shell) || !!(messageCapture && messageCapture.atTail),
  };
}

function _chatRestoreLayoutScroll(shell, capture) {
  if (!shell || !capture) return;
  var list = _chatShellPart(shell, '[data-chat-thread-list]', 'list');
  var messages = _chatShellPart(shell, '[data-chat-message-list]', 'messages');
  _chatRestoreScrollAnchor(list, 'data-chat-thread-id', capture.thread);
  _chatRestoreScrollAnchor(messages, 'data-chat-message-id', capture.messages, {
    pinTail: !!capture.messagesPinTail,
  });
  _chatUpdateMessageTailState(shell);
}

function _chatScheduleLayoutScrollRestore(shell, capture) {
  if (!shell || !capture) return;
  var restore = function() { _chatRestoreLayoutScroll(shell, capture); };
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(restore);
  } else if (typeof setTimeout === 'function') {
    setTimeout(restore, 0);
  } else {
    restore();
  }
}

function _chatApplyResponsiveLayout(shell, width) {
  if (!shell) return false;
  width = Number(width || 0);
  if (!Number.isFinite(width) || width <= 0) return false;
  var layout = width <= _CHAT_RESPONSIVE_NARROW_MAX_WIDTH ? 'narrow' : 'wide';
  var current = shell.getAttribute ? String(shell.getAttribute('data-chat-layout') || '') : '';
  if (current === layout) return false;
  var capture = _chatCaptureLayoutScroll(shell);
  if (shell.setAttribute) shell.setAttribute('data-chat-layout', layout);
  if (shell.classList && typeof shell.classList.toggle === 'function') {
    shell.classList.toggle('chat-panel-narrow', layout === 'narrow');
    shell.classList.toggle('chat-panel-wide', layout !== 'narrow');
  }
  _chatScheduleLayoutScrollRestore(shell, capture);
  return true;
}

function _chatEnsureResponsiveLayout(shell, root) {
  if (!shell) return;
  root = root || (shell.parentNode || null);
  shell._chatResponsiveRoot = root;
  if (!shell._chatResponsiveObserver && typeof ResizeObserver === 'function') {
    shell._chatResponsiveObserver = new ResizeObserver(function(entries) {
      var width = _chatResponsiveEntryWidth(entries && entries[0]);
      if (!width) width = _chatMeasuredPanelWidth(shell, shell._chatResponsiveRoot);
      _chatApplyResponsiveLayout(shell, width);
    });
    try {
      shell._chatResponsiveObserver.observe(root || shell);
    } catch (e) {
      try { shell._chatResponsiveObserver.observe(shell); } catch (_e) { /* ignore observer failures */ }
    }
  }
  _chatApplyResponsiveLayout(shell, _chatMeasuredPanelWidth(shell, root));
}

function _chatEsc(value) {
  if (typeof _agentPanelEsc === 'function') return _agentPanelEsc(value);
  if (typeof esc === 'function') return esc(value);
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _chatAttr(value) {
  if (typeof _agentPanelAttr === 'function') return _agentPanelAttr(value);
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _chatEventAttr(value) {
  if (typeof _agentPanelEventAttr === 'function') return _agentPanelEventAttr(value);
  return _chatAttr(value);
}

function _chatThreadMap() {
  if (!state || !state.agent_peer_threads || typeof state.agent_peer_threads !== 'object') return {};
  return state.agent_peer_threads;
}

function _chatTimestamp(value) {
  if (typeof _deltaTimestampValue === 'function') return _deltaTimestampValue(value);
  if (typeof value === 'number') return Number.isFinite(value) ? value : 0;
  var numeric = Number(value);
  if (Number.isFinite(numeric) && String(value || '').trim() !== '') return numeric;
  var parsed = Date.parse(String(value || ''));
  return Number.isFinite(parsed) ? parsed / 1000 : 0;
}

function _chatThreadActivity(thread) {
  thread = thread || {};
  var ts = _chatTimestamp(thread.last_activity_at);
  if (ts) return ts;
  var last = thread.last_message || {};
  return _chatTimestamp(last.timestamp || last.created_at || last.sent_at);
}

function _chatThreadId(thread, fallback) {
  return String((thread && thread.thread_id) || fallback || '').trim();
}

function _chatThreadCompare(a, b) {
  var at = _chatThreadActivity(a.thread);
  var bt = _chatThreadActivity(b.thread);
  if (at !== bt) return bt - at;
  var am = String((a.thread && a.thread.last_message_id) || '');
  var bm = String((b.thread && b.thread.last_message_id) || '');
  if (am !== bm) return bm.localeCompare(am);
  return String(b.id || '').localeCompare(String(a.id || ''));
}

function _chatThreads() {
  var map = _chatThreadMap();
  var items = [];
  for (var key in map) {
    var thread = map[key];
    if (!thread) continue;
    var id = _chatThreadId(thread, key);
    if (!id) continue;
    items.push({ id: id, thread: thread });
  }
  items.sort(_chatThreadCompare);
  return items;
}

function _chatThreadById(threadId) {
  threadId = String(threadId || '').trim();
  if (!threadId) return null;
  var map = _chatThreadMap();
  return map[threadId] || null;
}

function _chatMessageWindowSize() {
  var size = Number(_CHAT_MESSAGE_WINDOW_SIZE || 20);
  return Number.isFinite(size) && size > 0 ? Math.floor(size) : 20;
}

function _chatVisibleMessageCount(threadId, total) {
  threadId = String(threadId || '').trim();
  total = Math.max(0, Math.floor(Number(total || 0) || 0));
  if (!total) return 0;
  var windowSize = _chatMessageWindowSize();
  var base = Math.min(total, windowSize);
  var stored = threadId ? Number(_chatVisibleMessageCountByThread[threadId] || 0) : 0;
  var count = stored > 0 ? stored : base;
  return Math.max(base, Math.min(total, Math.floor(count)));
}

function _chatSetVisibleMessageCount(threadId, count, total) {
  threadId = String(threadId || '').trim();
  if (!threadId) return 0;
  total = Math.max(0, Math.floor(Number(total || 0) || 0));
  count = Math.max(0, Math.floor(Number(count || 0) || 0));
  var next = Math.min(total, Math.max(Math.min(total, _chatMessageWindowSize()), count));
  if (!total || next <= _chatMessageWindowSize()) {
    delete _chatVisibleMessageCountByThread[threadId];
  } else {
    _chatVisibleMessageCountByThread[threadId] = next;
  }
  return next;
}

function _chatLoadOlderMessagesIfNeeded(shell) {
  if (!shell) return false;
  if (shell._chatSuppressOlderMessageLoad || shell._chatLoadingOlderMessages) return false;
  var messages = _chatShellPart(shell, '[data-chat-message-list]', 'messages');
  if (!messages || typeof messages.scrollTop !== 'number') return false;
  if (Number(messages.scrollTop || 0) > _CHAT_MESSAGE_SCROLL_TOP_THRESHOLD) return false;
  var threadId = _chatCurrentRenderedThreadId(shell) || _chatSelectedThreadId;
  var thread = _chatThreadById(threadId);
  var rows = Array.isArray(thread && thread.messages) ? thread.messages : [];
  var current = _chatVisibleMessageCount(threadId, rows.length);
  if (current >= rows.length) return false;
  var next = Math.min(rows.length, current + _chatMessageWindowSize());
  _chatSetVisibleMessageCount(threadId, next, rows.length);
  shell._chatSuppressOlderMessageLoad = true;
  shell._chatLoadingOlderMessages = true;
  try {
    renderChatPanel();
  } finally {
    shell._chatLoadingOlderMessages = false;
    if (typeof setTimeout === 'function') {
      setTimeout(function() { shell._chatSuppressOlderMessageLoad = false; }, 100);
    } else {
      shell._chatSuppressOlderMessageLoad = false;
    }
  }
  return true;
}

function _chatEnsureSelectedThread(items) {
  items = Array.isArray(items) ? items : _chatThreads();
  if (_chatSelectedThreadId && _chatThreadById(_chatSelectedThreadId)) {
    return _chatSelectedThreadId;
  }
  _chatSelectedThreadId = items.length ? String(items[0].id || '') : '';
  return _chatSelectedThreadId;
}

function _chatFormatTime(ts) {
  ts = _chatTimestamp(ts);
  if (!ts) return '';
  if (typeof _agentPanelTimeAgo === 'function') return _agentPanelTimeAgo(ts);
  if (typeof _relativeTime === 'function') return _relativeTime(ts);
  var diff = (Date.now() / 1000) - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function _chatExactTimestamp(ts) {
  ts = _chatTimestamp(ts);
  if (!ts) return '';
  if (typeof _agentPanelTimestamp === 'function') return _agentPanelTimestamp(ts);
  return _chatFormatTime(ts);
}

function _chatParticipantName(participant) {
  participant = participant || {};
  return String(participant.name || participant.id || '').trim();
}

function _chatParticipantKind(participant) {
  participant = participant || {};
  var kind = String(participant.kind || '').trim();
  if (typeof _agentPanelMessageKindLabel === 'function') return _agentPanelMessageKindLabel(kind);
  return kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : 'Agent';
}

function _chatParticipantsText(thread) {
  var participants = Array.isArray(thread && thread.participants) ? thread.participants : [];
  var names = participants.map(_chatParticipantName).filter(Boolean);
  return names.join(' · ');
}

function _chatMessageRawText(message) {
  return String((message && (message.message || message.content || message.text)) || '');
}

function _chatMessageText(message) {
  return _chatMessageRawText(message).trim();
}

function _chatMessageBodyHtml(message) {
  var text = _chatMessageRawText(message);
  if (typeof torqueRenderMarkdownMessage === 'function') {
    return torqueRenderMarkdownMessage(text);
  }
  return _chatEsc(text);
}

function _chatCloseContextMenu() {
  if (typeof _closeCtxMenu === 'function') {
    _closeCtxMenu();
    return;
  }
  if (typeof closeContextMenu === 'function') {
    closeContextMenu();
    return;
  }
  var menu = (typeof document !== 'undefined' && document && document.getElementById)
    ? document.getElementById('ctx-menu')
    : null;
  if (menu && menu.classList) menu.classList.remove('open');
}

function _chatAdjustContextMenu(menu) {
  if (!menu) return;
  if (typeof _adjustCtxMenuOverflow === 'function') {
    _adjustCtxMenuOverflow();
    return;
  }
  var adjust = function() {
    if (!menu || typeof menu.getBoundingClientRect !== 'function') return;
    var rect = menu.getBoundingClientRect();
    var viewportWidth = (typeof window !== 'undefined' && window) ? Number(window.innerWidth || 0) : 0;
    var viewportHeight = (typeof window !== 'undefined' && window) ? Number(window.innerHeight || 0) : 0;
    if (viewportWidth && rect.right > viewportWidth) {
      menu.style.left = Math.max(0, viewportWidth - rect.width - 4) + 'px';
    }
    if (viewportHeight && rect.bottom > viewportHeight) {
      menu.style.top = Math.max(0, viewportHeight - rect.height - 4) + 'px';
    }
  };
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(adjust);
  else adjust();
}

function _chatMessageById(threadId, messageId) {
  var thread = _chatThreadById(threadId);
  var messages = Array.isArray(thread && thread.messages) ? thread.messages : [];
  var target = String(messageId || '').trim();
  if (!target) return null;
  for (var i = 0; i < messages.length; i++) {
    if (_chatMessageKey(messages[i], i) === target) return messages[i];
  }
  return null;
}

function chatMessageContextMenu(event, threadId, messageId) {
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  threadId = String(threadId || '').trim();
  messageId = String(messageId || '').trim();
  var menu = (typeof document !== 'undefined' && document && document.getElementById)
    ? document.getElementById('ctx-menu')
    : null;
  if (!threadId || !messageId || !menu) return false;
  var action = 'chatCopyMessage(' + JSON.stringify(threadId) + ',' + JSON.stringify(messageId) + ')';
  menu.innerHTML = '<button onclick="event.stopPropagation();' + _chatEsc(action) + '">Copy</button>';
  menu.style.top = ((event && Number.isFinite(Number(event.clientY))) ? Number(event.clientY) : 0) + 'px';
  var x = (event && Number.isFinite(Number(event.clientX))) ? Number(event.clientX) : 0;
  var viewportWidth = (typeof window !== 'undefined' && window) ? Number(window.innerWidth || 0) : 0;
  menu.style.left = Math.max(0, viewportWidth ? Math.min(x, viewportWidth - 140) : x) + 'px';
  if (menu.classList) menu.classList.add('open');
  _chatAdjustContextMenu(menu);
  return false;
}

function chatCopyMessage(threadId, messageId) {
  var message = _chatMessageById(threadId, messageId);
  var text = _chatMessageRawText(message);
  var clipboard = (typeof navigator !== 'undefined' && navigator) ? navigator.clipboard : null;
  var close = function() { _chatCloseContextMenu(); };
  if (clipboard && typeof clipboard.writeText === 'function') {
    var result = null;
    try {
      result = clipboard.writeText(text);
    } catch (_e) {
      close();
      return false;
    }
    if (result && typeof result.then === 'function') {
      result.then(close, close);
    } else {
      close();
    }
  } else {
    close();
  }
  return false;
}

function _chatFirstLine(value, limit) {
  var text = String(value || '').trim().split(/\r?\n/)[0] || '';
  limit = Math.max(12, Number(limit || 88) || 88);
  if (text.length <= limit) return text;
  return text.slice(0, limit - 1).trimEnd() + '…';
}

function _chatCountBadge(label, value, cls) {
  value = Number(value || 0);
  if (!Number.isFinite(value) || value <= 0) return '';
  return '<span class="chat-badge ' + _chatAttr(cls || '') + '">'
    + _chatEsc(label) + (value > 1 ? ' ' + value : '') + '</span>';
}

function _chatMessageKey(message, index) {
  message = message || {};
  var id = String(message.id || message.message_id || '').trim();
  if (id) return id;
  var threadId = String(message.thread_id || '').trim();
  var ts = _chatTimestamp(message.timestamp || message.created_at || message.sent_at);
  return (threadId ? threadId + ':' : '') + (ts || index) + ':' + index;
}

function _chatFindAnchor(container, attrName, value) {
  if (!container || !attrName || value == null) return null;
  value = String(value);
  var items = container.querySelectorAll ? container.querySelectorAll('[' + attrName + ']') : [];
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    if (item && String(item.getAttribute(attrName) || '') === value) return item;
  }
  return null;
}

function _chatCaptureScrollAnchor(container, attrName) {
  if (!container || typeof container.scrollTop !== 'number') return null;
  var scrollTop = Number(container.scrollTop || 0);
  var clientHeight = Number(container.clientHeight || 0);
  var scrollHeight = Number(container.scrollHeight || 0);
  var atTail = scrollHeight > 0 && (scrollTop + clientHeight >= scrollHeight - 12);
  var items = container.querySelectorAll ? container.querySelectorAll('[' + attrName + ']') : [];
  var anchorId = '';
  var anchorOffset = 0;
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    if (!item) continue;
    var top = Number(item.offsetTop || 0);
    var height = Number(item.offsetHeight || 0);
    if (top + height >= scrollTop) {
      anchorId = String(item.getAttribute(attrName) || '');
      anchorOffset = top - scrollTop;
      break;
    }
  }
  return {
    anchorId: anchorId,
    anchorOffset: anchorOffset,
    scrollTop: scrollTop,
    atTail: atTail,
  };
}

function _chatRestoreScrollAnchor(container, attrName, capture, opts) {
  if (!container || !capture) return;
  opts = opts || {};
  if (opts.pinTail || (capture.atTail && !opts.ignoreCaptureTail)) {
    container.scrollTop = Math.max(0, Number(container.scrollHeight || 0));
    return;
  }
  var restored = false;
  if (capture.anchorId) {
    var anchor = _chatFindAnchor(container, attrName, capture.anchorId);
    if (anchor) {
      container.scrollTop = Math.max(0, Number(anchor.offsetTop || 0) - Number(capture.anchorOffset || 0));
      restored = true;
    }
  }
  if (!restored) container.scrollTop = Math.max(0, Number(capture.scrollTop || 0));
}

function _chatSetHtml(el, html) {
  if (!el) return false;
  html = String(html || '');
  if (el._torqueLastHtml === html && el.innerHTML === html) return false;
  el.innerHTML = html;
  el._torqueLastHtml = html;
  return true;
}

function _chatEnsurePanelShell(root) {
  if (!root) return null;
  var shell = root.querySelector ? root.querySelector('[data-chat-panel]') : null;
  if (shell) return shell;
  if (root._chatShell && root._chatShell.parentNode === root) return root._chatShell;
  root.innerHTML = '';
  root._torqueLastHtml = '';
  if (typeof document === 'undefined' || typeof document.createElement !== 'function') return null;
  shell = document.createElement('div');
  shell.className = 'chat-panel';
  shell.setAttribute('data-chat-panel', '1');
  var threadPane = document.createElement('section');
  threadPane.className = 'chat-thread-pane';
  threadPane.setAttribute('aria-label', 'Chat threads');
  var listHeader = document.createElement('div');
  listHeader.className = 'chat-panel-header';
  listHeader.setAttribute('data-chat-list-header', '1');
  var list = document.createElement('div');
  list.className = 'chat-thread-list';
  list.setAttribute('data-chat-thread-list', '1');
  var messagePane = document.createElement('section');
  messagePane.className = 'chat-message-pane';
  messagePane.setAttribute('aria-label', 'Chat messages');
  var messageHeader = document.createElement('div');
  messageHeader.className = 'chat-message-header';
  messageHeader.setAttribute('data-chat-message-header', '1');
  var messages = document.createElement('div');
  messages.className = 'chat-message-list';
  messages.setAttribute('data-chat-message-list', '1');
  threadPane.appendChild(listHeader);
  threadPane.appendChild(list);
  messagePane.appendChild(messageHeader);
  messagePane.appendChild(messages);
  shell.appendChild(threadPane);
  shell.appendChild(messagePane);
  shell._chatParts = {
    listHeader: listHeader,
    list: list,
    messageHeader: messageHeader,
    messages: messages,
  };
  root._chatShell = shell;
  root.appendChild(shell);
  _chatAttachMessageTailTracker(shell);
  _chatEnsureResponsiveLayout(shell, root);
  return (root.querySelector ? root.querySelector('[data-chat-panel]') : null) || shell;
}

function _chatShellPart(shell, selector, key) {
  if (!shell) return null;
  var found = shell.querySelector ? shell.querySelector(selector) : null;
  if (found) return found;
  return (shell._chatParts && shell._chatParts[key]) || null;
}

function _chatCaptureExpandedContext(root) {
  if (!root || !root.querySelectorAll) return;
  var details = root.querySelectorAll('details[data-chat-context-id]') || [];
  for (var i = 0; i < details.length; i++) {
    var item = details[i];
    var id = item && item.getAttribute ? String(item.getAttribute('data-chat-context-id') || '') : '';
    if (id) _chatExpandedContextById[id] = !!item.open;
  }
}

function chatContextToggled(contextId, open) {
  contextId = String(contextId || '').trim();
  if (!contextId) return;
  _chatExpandedContextById[contextId] = !!open;
}

function _chatContextChips(kind, values, labelFn) {
  values = Array.isArray(values) ? values.filter(function(value) {
    return String(value || '').trim();
  }) : [];
  if (!values.length) return '';
  if (typeof _agentPanelRenderContextChips === 'function') {
    return _agentPanelRenderContextChips(kind, values, labelFn);
  }
  var html = '';
  for (var i = 0; i < values.length; i++) {
    html += '<span class="agent-panel-message-context-chip agent-panel-message-context-'
      + _chatAttr(kind) + '"><span class="agent-panel-message-context-kind">'
      + _chatEsc(kind) + '</span>' + _chatEsc(values[i]) + '</span>';
  }
  return html;
}

function _chatMessageContextHtml(message, messageId) {
  message = message || {};
  var context = (message.context && typeof message.context === 'object') ? message.context : {};
  var taskIds = Array.isArray(message.context_task_ids)
    ? message.context_task_ids
    : (Array.isArray(context.task_ids) ? context.task_ids : []);
  var engineerIds = Array.isArray(message.context_engineer_ids)
    ? message.context_engineer_ids
    : (Array.isArray(context.engineer_ids) ? context.engineer_ids : []);
  var decisionIds = Array.isArray(message.context_decision_ids)
    ? message.context_decision_ids
    : (Array.isArray(context.decision_ids) ? context.decision_ids : []);
  var summary = String(message.context_summary != null ? message.context_summary : (context.summary || '')).trim();
  var chips = '';
  chips += _chatContextChips('task', taskIds, typeof _agentPanelTaskLabel === 'function' ? _agentPanelTaskLabel : null);
  chips += _chatContextChips('engineer', engineerIds, typeof _agentPanelAgentLabel === 'function' ? _agentPanelAgentLabel : null);
  chips += _chatContextChips('decision', decisionIds, typeof _agentPanelDecisionLabel === 'function' ? _agentPanelDecisionLabel : null);
  if (!chips && !summary) return '';
  var id = String(messageId || '').trim();
  var open = !!_chatExpandedContextById[id];
  var html = '<details class="chat-message-context" data-chat-context-id="' + _chatAttr(id) + '"'
    + (open ? ' open' : '')
    + ' ontoggle="' + _chatEventAttr('chatContextToggled(' + JSON.stringify(id) + ', this.open)') + '">';
  html += '<summary>Context details</summary>';
  html += '<div class="agent-panel-message-context-preview">';
  if (summary) {
    html += '<div class="agent-panel-message-context-summary">' + _chatEsc(summary) + '</div>';
  }
  if (chips) html += '<div class="agent-panel-message-context-chips">' + chips + '</div>';
  html += '</div></details>';
  return html;
}

function _chatMessageAttributionHtml(message) {
  message = message || {};
  var senderName = String(message.sender_name || message.sender_id || '').trim() || 'Unknown';
  var recipientName = String(message.recipient_name || message.recipient_id || '').trim() || 'Unknown';
  return '<span class="agent-panel-message-attribution agent-panel-message-attribution-out">'
    + '<span class="agent-panel-message-attribution-label">From:</span>'
    + '<span class="agent-panel-message-attribution-name">' + _chatEsc(senderName) + '</span>'
    + '</span>'
    + '<span class="agent-panel-message-attribution agent-panel-message-attribution-in">'
    + '<span class="agent-panel-message-attribution-label">To:</span>'
    + '<span class="agent-panel-message-attribution-name">' + _chatEsc(recipientName) + '</span>'
    + '</span>';
}

function _chatMessageMetaHtml(message) {
  message = message || {};
  var html = '';
  if (message.ack_required) {
    html += '<span class="chat-message-badge chat-message-badge-ack">Ack required</span>';
  }
  var deliveryState = String(message.delivery_state || '').trim();
  if (deliveryState && deliveryState !== 'delivered') {
    html += '<span class="chat-message-badge chat-message-badge-pending">' + _chatEsc(deliveryState) + '</span>';
  }
  return html;
}

function _chatMessageActionLabel(action) {
  action = String(action || 'message').trim();
  if (!action) return 'message';
  if (action === 'engineer_peer_notify') return 'engineer peer notify';
  if (action === 'engineer_peer_reply') return 'engineer peer reply';
  if (typeof _agentPanelMessageActionLabel === 'function') {
    return _agentPanelMessageActionLabel(action);
  }
  return action.replace(/[_-]+/g, ' ');
}

function _chatMessageSenderLabel(message) {
  message = message || {};
  return String(message.sender_name || message.sender_id || '').trim()
    || _chatParticipantKind({ kind: message.sender_kind })
    || 'Unknown';
}

function _chatMessageRecipientLabel(message) {
  message = message || {};
  return String(message.recipient_name || message.recipient_id || '').trim();
}

function _chatStablePairIds(thread, message) {
  var ids = [];
  var participants = Array.isArray(thread && thread.participants) ? thread.participants : [];
  for (var i = 0; i < participants.length; i++) {
    var pid = String((participants[i] && participants[i].id) || '').trim();
    if (pid && ids.indexOf(pid) < 0) ids.push(pid);
  }
  message = message || {};
  var senderId = String(message.sender_id || '').trim();
  var recipientId = String(message.recipient_id || '').trim();
  if (senderId && ids.indexOf(senderId) < 0) ids.push(senderId);
  if (recipientId && ids.indexOf(recipientId) < 0) ids.push(recipientId);
  ids.sort();
  return ids;
}

function _chatMessageSide(message, thread) {
  message = message || {};
  var senderKind = String(message.sender_kind || '').trim().toLowerCase();
  var recipientKind = String(message.recipient_kind || '').trim().toLowerCase();
  var senderId = String(message.sender_id || '').trim();
  if ((senderKind === 'architect' && recipientKind === 'architect')
      || (senderKind === 'engineer' && recipientKind === 'engineer')) {
    var pairIds = _chatStablePairIds(thread, message);
    if (pairIds.length >= 2 && senderId) {
      return senderId === pairIds[0] ? 'left' : 'right';
    }
    return senderKind === 'architect' ? 'right' : 'left';
  }
  if (senderKind === 'architect') return 'right';
  if (senderKind === 'engineer' || senderKind === 'worker') return 'left';
  if (recipientKind === 'architect') return 'left';
  var fallbackIds = _chatStablePairIds(thread, message);
  if (fallbackIds.length >= 2 && senderId) {
    return senderId === fallbackIds[0] ? 'left' : 'right';
  }
  return 'left';
}

function _chatMessageCardHtml(message, index, thread) {
  message = message || {};
  var messageId = _chatMessageKey(message, index);
  var threadId = _chatThreadId(thread, message.thread_id || _chatSelectedThreadId);
  var senderKind = String(message.sender_kind || '').trim() || 'agent';
  var side = _chatMessageSide(message, thread);
  var timestamp = _chatTimestamp(message.timestamp || message.created_at || message.sent_at);
  var recipient = _chatMessageRecipientLabel(message);
  var meta = _chatMessageMetaHtml(message);
  var timeLabel = _chatExactTimestamp(timestamp);
  var html = '<div class="chat-message-row chat-message-row-' + _chatAttr(side)
    + '" data-chat-message-id="' + _chatAttr(messageId) + '"'
    + ' data-chat-thread-id="' + _chatAttr(threadId) + '"'
    + ' data-chat-message-side="' + _chatAttr(side) + '"'
    + ' data-chat-sender-kind="' + _chatAttr(senderKind) + '"'
    + ' oncontextmenu="' + _chatEventAttr('return chatMessageContextMenu(event, ' + JSON.stringify(threadId) + ', ' + JSON.stringify(messageId) + ')') + '">';
  html += '<div class="chat-message-bubble chat-message-bubble-' + _chatAttr(side) + '">';
  html += '<div class="chat-message-bubble-meta">';
  html += '<span class="chat-message-sender">' + _chatEsc(_chatMessageSenderLabel(message)) + '</span>';
  html += '<span class="chat-message-kind">' + _chatEsc(_chatParticipantKind({ kind: senderKind })) + '</span>';
  html += '<span class="chat-message-action">' + _chatEsc(_chatMessageActionLabel(message.action)) + '</span>';
  if (meta) html += meta;
  if (timeLabel) html += '<span class="chat-message-time">' + _chatEsc(timeLabel) + '</span>';
  html += '</div>';
  html += '<div class="chat-message-body torque-markdown">' + _chatMessageBodyHtml(message) + '</div>';
  if (recipient) {
    html += '<div class="chat-message-recipient">To ' + _chatEsc(recipient) + '</div>';
  }
  html += _chatMessageContextHtml(message, messageId);
  html += '</div></div>';
  return html;
}

function _chatThreadRowHtml(item, selectedId) {
  var thread = item.thread || {};
  var id = String(item.id || '');
  var selected = id === selectedId;
  var last = thread.last_message || {};
  var title = String(thread.title || id || 'Thread');
  var participants = _chatParticipantsText(thread);
  var lastText = _chatFirstLine(_chatMessageText(last), 96) || 'No messages yet';
  var time = _chatFormatTime(thread.last_activity_at || last.timestamp || 0);
  var count = Number(thread.message_count || (Array.isArray(thread.messages) ? thread.messages.length : 0) || 0);
  var onclick = 'chatSelectThread(' + JSON.stringify(id) + ')';
  var onkeydown = 'if(event.key==="Enter"||event.key===" "){event.preventDefault();chatSelectThread(' + JSON.stringify(id) + ')}';
  var html = '<div class="chat-thread-row' + (selected ? ' selected' : '') + '"'
    + ' role="button" tabindex="0" data-chat-thread-id="' + _chatAttr(id) + '"'
    + ' aria-selected="' + (selected ? 'true' : 'false') + '"'
    + ' onclick="' + _chatEventAttr(onclick) + '"'
    + ' onkeydown="' + _chatEventAttr(onkeydown) + '">';
  html += '<div class="chat-thread-row-top">'
    + '<div class="chat-thread-title">' + _chatEsc(title) + '</div>'
    + '<div class="chat-thread-time">' + _chatEsc(time) + '</div>'
    + '</div>';
  if (participants) html += '<div class="chat-thread-participants">' + _chatEsc(participants) + '</div>';
  html += '<div class="chat-thread-last">' + _chatEsc(lastText) + '</div>';
  html += '<div class="chat-thread-badges">'
    + '<span class="chat-badge chat-badge-count">' + _chatEsc(count + ' msg' + (count === 1 ? '' : 's')) + '</span>'
    + _chatCountBadge('Ack required', thread.ack_required_count, 'chat-badge-ack')
    + _chatCountBadge('Pending', thread.pending_delivery_count, 'chat-badge-pending')
    + '</div>';
  html += '</div>';
  return html;
}

function _chatThreadListHtml(items, selectedId) {
  if (!items.length) {
    return '<div class="chat-empty-state">No architect or engineer peer messages yet.</div>';
  }
  var html = '';
  for (var i = 0; i < items.length; i++) {
    html += _chatThreadRowHtml(items[i], selectedId);
  }
  return html;
}

function _chatThreadHeaderHtml(items) {
  var total = items.length;
  var ackTotal = 0;
  var pendingTotal = 0;
  for (var i = 0; i < items.length; i++) {
    ackTotal += Number((items[i].thread || {}).ack_required_count || 0);
    pendingTotal += Number((items[i].thread || {}).pending_delivery_count || 0);
  }
  return '<div class="chat-panel-title-wrap">'
    + '<div class="chat-panel-title">Chat</div>'
    + '<div class="chat-panel-subtitle">Read-only peer inbox</div>'
    + '</div>'
    + '<div class="chat-panel-summary">'
    + '<span class="chat-badge chat-badge-count">' + _chatEsc(total + ' thread' + (total === 1 ? '' : 's')) + '</span>'
    + _chatCountBadge('Ack required', ackTotal, 'chat-badge-ack')
    + _chatCountBadge('Pending', pendingTotal, 'chat-badge-pending')
    + '</div>';
}

function _chatSelectedHeaderHtml(thread) {
  if (!thread) {
    return '<div class="chat-message-title-wrap"><div class="chat-message-title">No thread selected</div>'
      + '<div class="chat-message-subtitle">Select a thread to inspect messages.</div></div>';
  }
  var participants = Array.isArray(thread.participants) ? thread.participants : [];
  var chips = '';
  for (var i = 0; i < participants.length; i++) {
    var participant = participants[i] || {};
    chips += '<span class="chat-participant-chip">'
      + '<span class="chat-participant-kind">' + _chatEsc(_chatParticipantKind(participant)) + '</span>'
      + _chatEsc(_chatParticipantName(participant) || 'Unknown')
      + '</span>';
  }
  var count = Number(thread.message_count || (Array.isArray(thread.messages) ? thread.messages.length : 0) || 0);
  return '<div class="chat-message-title-wrap">'
    + '<div class="chat-message-title">' + _chatEsc(thread.title || thread.thread_id || 'Thread') + '</div>'
    + '<div class="chat-message-subtitle">' + _chatEsc(thread.group || 'All groups') + '</div>'
    + '</div>'
    + '<div class="chat-message-header-meta">'
    + '<div class="chat-participant-list">' + chips + '</div>'
    + '<div class="chat-thread-badges">'
    + '<span class="chat-badge chat-badge-count">' + _chatEsc(count + ' msg' + (count === 1 ? '' : 's')) + '</span>'
    + _chatCountBadge('Ack required', thread.ack_required_count, 'chat-badge-ack')
    + _chatCountBadge('Pending', thread.pending_delivery_count, 'chat-badge-pending')
    + '</div></div>';
}

function _chatMessagesHtml(thread) {
  if (!thread) {
    return '<div class="chat-empty-state chat-empty-state-wide">No thread selected.</div>';
  }
  var messages = Array.isArray(thread.messages) ? thread.messages : [];
  var threadId = _chatThreadId(thread, _chatSelectedThreadId);
  var visibleCount = _chatVisibleMessageCount(threadId, messages.length);
  var start = Math.max(0, messages.length - visibleCount);
  var html = '';
  if (start > 0) {
    html += '<div class="chat-older-affordance" data-chat-window-affordance="1">'
      + 'Scroll up to load older messages'
      + '</div>';
  } else if (thread.truncated) {
    html += '<div class="chat-older-affordance">Older messages exist outside this loaded tail.</div>';
  }
  if (!messages.length) {
    html += '<div class="chat-empty-state chat-empty-state-wide">No messages in this thread yet.</div>';
    return html;
  }
  for (var i = start; i < messages.length; i++) {
    html += _chatMessageCardHtml(messages[i], i, thread);
  }
  return html;
}

function chatSelectThread(threadId) {
  threadId = String(threadId || '').trim();
  if (!threadId || !_chatThreadById(threadId)) return;
  if (_chatSelectedThreadId === threadId) return;
  _chatSelectedThreadId = threadId;
  renderChatPanel();
}

function renderChatPanel() {
  var root = document.getElementById('panel-chat');
  if (!root) return;
  var shell = _chatEnsurePanelShell(root);
  if (!shell || !shell.querySelector) return;
  _chatCaptureExpandedContext(root);

  var items = _chatThreads();
  var previouslyRenderedThread = String(root.getAttribute && root.getAttribute('data-chat-rendered-thread-id') || '');
  var selectedId = _chatEnsureSelectedThread(items);
  var selectedThread = selectedId ? _chatThreadById(selectedId) : null;

  var listHeader = _chatShellPart(shell, '[data-chat-list-header]', 'listHeader');
  var list = _chatShellPart(shell, '[data-chat-thread-list]', 'list');
  var messageHeader = _chatShellPart(shell, '[data-chat-message-header]', 'messageHeader');
  var messages = _chatShellPart(shell, '[data-chat-message-list]', 'messages');
  _chatAttachMessageTailTracker(shell);

  var threadCapture = _chatCaptureScrollAnchor(list, 'data-chat-thread-id');
  var preserveMessages = previouslyRenderedThread && previouslyRenderedThread === selectedId;
  var loadingOlderMessages = preserveMessages && !!shell._chatLoadingOlderMessages;
  var messagesPinTail = !loadingOlderMessages && preserveMessages && _chatStoredMessageTailPinned(shell);
  var messageCapture = preserveMessages
    ? _chatCaptureScrollAnchor(messages, 'data-chat-message-id')
    : null;

  _chatSetHtml(listHeader, _chatThreadHeaderHtml(items));
  _chatSetHtml(list, _chatThreadListHtml(items, selectedId));
  _chatSetHtml(messageHeader, _chatSelectedHeaderHtml(selectedThread));
  _chatSetHtml(messages, _chatMessagesHtml(selectedThread));

  _chatRestoreScrollAnchor(list, 'data-chat-thread-id', threadCapture || { scrollTop: 0 });
  if (messageCapture) {
    _chatRestoreScrollAnchor(messages, 'data-chat-message-id', messageCapture, {
      pinTail: !!messagesPinTail,
      ignoreCaptureTail: !!loadingOlderMessages,
    });
  } else if (messages && typeof messages.scrollTop === 'number') {
    messages.scrollTop = Math.max(0, Number(messages.scrollHeight || 0));
  }
  if (root.setAttribute) root.setAttribute('data-chat-rendered-thread-id', selectedId || '');
  _chatUpdateMessageTailState(shell);
  _chatEnsureResponsiveLayout(shell, root);
}

/* Embedded terminal workspace for standalone PTY mode */
let _embeddedTerminal = null;
let _embeddedTerminalFit = null;
let _embeddedTerminalWs = null;
let _embeddedTerminalSessionKey = '';
let _embeddedTerminalSessions = Object.create(null);
let _embeddedTerminalResizeObserver = null;
let _embeddedTerminalDataHandler = null;
let _embeddedTerminalPendingFocusKey = '';
let _embeddedTerminalDropSurface = null;
let _embeddedTerminalDropHandlers = null;
let _embeddedTerminalDropDepth = 0;
let _terminalComposeDrafts = Object.create(null);
let _terminalComposeAttachments = Object.create(null);
let _terminalComposeErrors = Object.create(null);
let _terminalComposeRecall = Object.create(null);
let _terminalComposeHistoryOpenCellId = '';
let _terminalComposeTaskDropdownCellId = '';
let _terminalComposeTaskDropdownIdx = -1;
let _terminalComposeTaskDropdownResults = [];
let _terminalDirectMessageSelectedByAgent = Object.create(null);
let _terminalDirectMessageReplyToByAgent = Object.create(null);
let _terminalDirectMessageVisibleCountByAgent = Object.create(null);
// Sticky "follow the bottom" intent per agent. Set true when the user is at the
// tail, false when they deliberately scroll up. Restore consults this rather
// than recomputing at-tail from the DOM every render: an instantaneous-only
// measure drops the tail whenever a render catches the viewport a few px off
// the bottom (worker activity / streamed messages / composing), which made the
// DM panel appear to "scroll up" and forced constant manual scroll-down.
let _terminalDirectMessagePinnedToTailByAgent = Object.create(null);
let _terminalDirectMessageIdempotencyCounter = 0;
let _terminalDirectMessagesResizeDrag = null;
let _terminalDirectMessagePointerDown = null;
let _lastAppliedXtermScrollback = null;

var TERMINAL_DIRECT_MESSAGES_WINDOW_SIZE = 20;
var TERMINAL_DIRECT_MESSAGES_SCROLL_TOP_THRESHOLD = 36;
// How close to the bottom still counts as "at the tail". Generous enough that a
// render landing a hair off the bottom (sub-pixel rounding, a freshly appended
// message, a window-slide) is still treated as tailing rather than detaching.
var TERMINAL_DIRECT_MESSAGES_TAIL_THRESHOLD_PX = 24;
var TERMINAL_DIRECT_MESSAGES_MIN_HEIGHT = 112;
var TERMINAL_DIRECT_MESSAGES_DEFAULT_HEIGHT = 190;
var TERMINAL_DIRECT_MESSAGES_MAX_HEIGHT_FALLBACK = 420;
var TERMINAL_DIRECT_MESSAGE_CLICK_DRAG_THRESHOLD_PX = 4;
var TERMINAL_DIRECT_MESSAGE_CLICK_DRAG_DURATION_MS = 650;

var TERMINAL_COMPOSE_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024;
var TERMINAL_COMPOSE_ATTACHMENT_MIME_TYPES = {
  'image/png': true,
  'image/jpeg': true,
  'image/webp': true,
  'image/gif': true,
};

var XTERM_SCROLLBACK_DEFAULT = 2000;
var XTERM_SCROLLBACK_MIN = 100;
var XTERM_SCROLLBACK_MAX = 100000;
var EMBEDDED_TERMINAL_TAIL_THRESHOLD_PX = 8;
var EMBEDDED_TERMINAL_USER_SCROLL_INTENT_MS = 900;

function _xtermScrollbackFromSettings(settings) {
  var raw = settings && settings.xterm_scrollback;
  var value = Number(raw);
  if (!Number.isFinite(value)) return XTERM_SCROLLBACK_DEFAULT;
  value = Math.floor(value);
  if (value < XTERM_SCROLLBACK_MIN || value > XTERM_SCROLLBACK_MAX) {
    return XTERM_SCROLLBACK_DEFAULT;
  }
  return value;
}

function _currentXtermScrollback() {
  return _xtermScrollbackFromSettings(
    state && state.global_settings ? state.global_settings : null
  );
}

function _applyEmbeddedTerminalScrollbackFromSettings() {
  const scrollback = _currentXtermScrollback();
  if (_lastAppliedXtermScrollback === scrollback) return;
  let applied = false;
  for (const key in _embeddedTerminalSessions) {
    const entry = _embeddedTerminalSessions[key];
    if (entry && entry.terminal && entry.terminal.options) {
      entry.terminal.options.scrollback = scrollback;
      applied = true;
    }
  }
  if (!applied && _embeddedTerminal && _embeddedTerminal.options) {
    _embeddedTerminal.options.scrollback = scrollback;
  }
  _lastAppliedXtermScrollback = scrollback;
}

function isEmbeddedTerminalMode() {
  return !!(state && state.runtime && state.runtime.embedded_terminal);
}

function _terminalCurrentGroupName() {
  if (typeof _singleGroupModeEnabled === 'function'
      && _singleGroupModeEnabled()
      && typeof _activeGroup === 'function') {
    return _activeGroup() || '';
  }
  if (selectedTerminalId && state.agents && state.agents[selectedTerminalId]) {
    return state.agents[selectedTerminalId].group || '';
  }
  if (selectedAgentId && state.agents && state.agents[selectedAgentId]) {
    return state.agents[selectedAgentId].group || '';
  }
  if (state && state.active_session_id && state.agents) {
    for (const id in state.agents) {
      if (state.agents[id].session_id === state.active_session_id) {
        return state.agents[id].group || '';
      }
    }
  }
  const groups = state && state.groups ? Object.keys(state.groups) : [];
  return groups.length ? groups[0] : '';
}

function _terminalTargetAgent(cell) {
  if (!cell || !state || !state.agents) return null;
  if (cell.cell_type === 'agent') return cell;
  if (cell.parent_id && state.agents[cell.parent_id]) {
    return state.agents[cell.parent_id];
  }
  return null;
}

function _terminalDirectMessageAgent(cell) {
  const agent = _terminalTargetAgent(cell);
  if (!agent || agent.cell_type !== 'agent') return null;
  const kind = String(agent.kind || '').trim().toLowerCase();
  if (!kind) return agent;
  return (kind === 'architect' || kind === 'engineer' || kind === 'worker')
    ? agent
    : null;
}

function _terminalDirectMessageId(row) {
  return String((row && (row.message_id || row.id)) || '').trim();
}

function _terminalDirectMessageTimestamp(row) {
  const value = row && (row.created_at || row.timestamp || row.sent_at);
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || value <= 0) return 0;
    return value > 100000000000 ? value / 1000 : value;
  }
  const numeric = Number(value);
  if (Number.isFinite(numeric) && String(value || '').trim() !== '') {
    if (numeric <= 0) return 0;
    return numeric > 100000000000 ? numeric / 1000 : numeric;
  }
  const parsed = Date.parse(String(value || ''));
  return Number.isFinite(parsed) ? parsed / 1000 : 0;
}

function _terminalDirectMessagesForAgent(agentId) {
  const id = String(agentId || '').trim();
  const rows = state && state.direct_messages_by_agent && Array.isArray(state.direct_messages_by_agent[id])
    ? state.direct_messages_by_agent[id].slice()
    : [];
  rows.sort(function(a, b) {
    const at = _terminalDirectMessageTimestamp(a);
    const bt = _terminalDirectMessageTimestamp(b);
    if (at !== bt) return at - bt;
    return _terminalDirectMessageId(a).localeCompare(_terminalDirectMessageId(b));
  });
  return rows;
}

function _terminalDirectMessagesWindowSize() {
  const size = Number(TERMINAL_DIRECT_MESSAGES_WINDOW_SIZE || 20);
  return Number.isFinite(size) && size > 0 ? Math.floor(size) : 20;
}

function _terminalDirectMessagesVisibleCount(agentId, total) {
  const id = String(agentId || '').trim();
  total = Math.max(0, Math.floor(Number(total || 0) || 0));
  if (!total) return 0;
  const windowSize = _terminalDirectMessagesWindowSize();
  const base = Math.min(total, windowSize);
  const stored = id
    ? Number(_terminalDirectMessageVisibleCountByAgent[id] || 0)
    : 0;
  const count = stored > 0 ? stored : base;
  return Math.max(base, Math.min(total, Math.floor(count)));
}

function _terminalDirectMessagesSetVisibleCount(agentId, count, total) {
  const id = String(agentId || '').trim();
  if (!id) return 0;
  total = Math.max(0, Math.floor(Number(total || 0) || 0));
  count = Math.max(0, Math.floor(Number(count || 0) || 0));
  const next = Math.min(total, Math.max(Math.min(total, _terminalDirectMessagesWindowSize()), count));
  if (!total || next <= _terminalDirectMessagesWindowSize()) {
    delete _terminalDirectMessageVisibleCountByAgent[id];
  } else {
    _terminalDirectMessageVisibleCountByAgent[id] = next;
  }
  return next;
}

function _terminalDirectMessageText(row) {
  return String((row && (row.message || row.text || row.body)) || '');
}

function _terminalDirectMessageType(row) {
  return String((row && row.message_type) || 'message').trim().toLowerCase() || 'message';
}

function _terminalDirectMessageBodyHtml(row) {
  const text = _terminalDirectMessageText(row);
  if (typeof torqueRenderMarkdownMessage === 'function') {
    return torqueRenderMarkdownMessage(text);
  }
  return esc(text).replace(/\n/g, '<br>');
}

function _terminalDirectMessageDirection(row, agent) {
  const type = _terminalDirectMessageType(row);
  if (type === 'system') return 'system';
  const senderKind = String((row && row.sender_kind) || '').trim().toLowerCase();
  const recipientKind = String((row && row.recipient_kind) || '').trim().toLowerCase();
  const senderId = String((row && row.sender_id) || '').trim();
  const agentId = String((agent && agent.id) || '').trim();
  if (senderKind === 'user') return 'user-to-agent';
  if (recipientKind === 'user') return 'agent-to-user';
  if (agentId && senderId === agentId) return 'agent-to-user';
  return 'user-to-agent';
}

function _terminalDirectMessageSenderLabel(row, agent) {
  const direction = _terminalDirectMessageDirection(row, agent);
  if (direction === 'system') return 'System';
  if (direction === 'user-to-agent') {
    return (row && row.sender_name) || 'You';
  }
  return (row && row.sender_name) || (agent && agent.name) || 'Agent';
}

function _terminalDirectMessageTimeLabel(row) {
  const ts = _terminalDirectMessageTimestamp(row);
  if (!ts) return '';
  try {
    return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch (_e) {
    return '';
  }
}

function _terminalDirectMessageTypeLabel(row) {
  const type = _terminalDirectMessageType(row);
  if (type === 'ask') {
    return (row && (row.blocking || row.ack_required)) ? 'Blocking ask' : 'Ask';
  }
  if (type === 'ask_reply') return 'Ask reply';
  if (type === 'system') return 'System';
  if (type !== 'message') return type.replace(/_/g, ' ');
  return '';
}

function _terminalDirectMessagePreview(row) {
  const text = _terminalDirectMessageText(row).replace(/\s+/g, ' ').trim();
  if (!text) return 'message';
  return text.length > 80 ? text.slice(0, 77) + '\u2026' : text;
}

function _terminalDirectMessageById(agentId, messageId) {
  const target = String(messageId || '').trim();
  if (!target) return null;
  const rows = _terminalDirectMessagesForAgent(agentId);
  for (let i = 0; i < rows.length; i++) {
    if (_terminalDirectMessageId(rows[i]) === target) return rows[i];
  }
  return null;
}

function _renderTerminalDirectMessageRow(row, agent) {
  const id = _terminalDirectMessageId(row);
  if (!id) return '';
  const type = _terminalDirectMessageType(row);
  const direction = _terminalDirectMessageDirection(row, agent);
  const selected = _terminalDirectMessageSelectedByAgent[String((agent && agent.id) || '')] === id;
  const typeLabel = _terminalDirectMessageTypeLabel(row);
  const timeLabel = _terminalDirectMessageTimeLabel(row);
  const askReply = type === 'ask'
    ? '<button type="button" class="terminal-direct-message-reply"'
      + ' onmousedown="return terminalDirectMessageMouseDown(event)"'
      + ' onclick="return terminalDirectMessageReply(event, \'' + esc(agent.id) + '\', \'' + esc(id) + '\')">'
      + 'Reply</button>'
    : '';
  return ''
    + '<div class="terminal-direct-message terminal-direct-message--' + esc(direction)
    + ' terminal-direct-message--' + esc(type.replace(/[^a-z0-9_-]+/g, '-'))
    + (selected ? ' selected' : '')
    + '" data-direct-message-id="' + esc(id) + '"'
    + ' data-direct-message-agent-id="' + esc((agent && agent.id) || '') + '"'
    + ' data-terminal-dm-anchor="' + esc(id) + '"'
    + ' role="button" tabindex="0"'
    + ' onmousedown="return terminalDirectMessageMouseDown(event)"'
    + ' oncontextmenu="return terminalDirectMessageContextMenu(event, \'' + esc(agent.id) + '\', \'' + esc(id) + '\')"'
    + ' onclick="return terminalDirectMessageSelect(event, \'' + esc(agent.id) + '\', \'' + esc(id) + '\')"'
    + ' onkeydown="terminalDirectMessageKeydown(event, \'' + esc(agent.id) + '\', \'' + esc(id) + '\')">'
    + '  <div class="terminal-direct-message-meta">'
    + '    <span class="terminal-direct-message-sender">' + esc(_terminalDirectMessageSenderLabel(row, agent)) + '</span>'
    + (typeLabel
      ? '    <span class="terminal-direct-message-badge">' + esc(typeLabel) + '</span>'
      : '')
    + (timeLabel
      ? '    <span class="terminal-direct-message-time">' + esc(timeLabel) + '</span>'
      : '')
    + '  </div>'
    + '  <div class="terminal-direct-message-body torque-markdown">' + _terminalDirectMessageBodyHtml(row) + '</div>'
    + (askReply ? '  <div class="terminal-direct-message-actions">' + askReply + '</div>' : '')
    + '</div>';
}

function _renderTerminalDirectMessagesHtml(agent) {
  const agentId = String((agent && agent.id) || '');
  const rows = _terminalDirectMessagesForAgent(agentId);
  const visibleCount = _terminalDirectMessagesVisibleCount(agentId, rows.length);
  const start = Math.max(0, rows.length - visibleCount);
  const visibleRows = rows.slice(start);
  let body = '';
  if (!rows.length) {
    body = '<div class="terminal-direct-messages-empty">No direct messages yet.</div>';
  } else {
    if (start > 0) {
      body += '<div class="terminal-direct-messages-window-affordance"'
        + ' data-terminal-dm-window-affordance="1">'
        + 'Scroll up to load older messages'
        + '</div>';
    }
    for (let i = 0; i < visibleRows.length; i++) {
      body += _renderTerminalDirectMessageRow(visibleRows[i], agent);
    }
  }
  return ''
    + '<div class="terminal-direct-messages-resizer" role="separator"'
    + ' aria-orientation="horizontal" aria-label="Resize direct messages"'
    + ' title="Resize direct messages" tabindex="0" data-terminal-direct-messages-resizer'
    + ' onmousedown="terminalDirectMessagesResizeStart(event)"'
    + ' onkeydown="terminalDirectMessagesResizeKeydown(event)">'
    + '  <div class="terminal-direct-messages-resizer-grip" aria-hidden="true"></div>'
    + '</div>'
    + '<section class="terminal-direct-messages" data-agent-id="' + esc(agentId) + '"'
    + ' aria-label="Direct messages with ' + esc((agent && agent.name) || 'agent') + '">'
    + '  <div class="terminal-direct-messages-header">'
    + '    <span class="terminal-direct-messages-title">Direct messages</span>'
    + '    <span class="terminal-direct-messages-peer">' + esc((agent && agent.name) || 'Agent') + '</span>'
    + '  </div>'
    + '  <div class="terminal-direct-messages-list" role="log" aria-live="polite" data-agent-id="' + esc(agentId) + '">'
    + body
    + '  </div>'
    + '</section>';
}

function _renderTerminalDirectMessages(root, cell) {
  if (!root) return;
  const agent = _terminalDirectMessageAgent(cell);
  if (!agent) {
    if (root.innerHTML !== '') root.innerHTML = '';
    root._torqueLastHtml = '';
    root.hidden = true;
    if (root.dataset) root.dataset.agentId = '';
    return;
  }
  const previous = _captureTerminalDirectMessagesState(root);
  const html = _renderTerminalDirectMessagesHtml(agent);
  root.hidden = false;
  if (root.dataset) root.dataset.agentId = String(agent.id || '');
  let changed = false;
  if (root._torqueLastHtml !== html || root.innerHTML !== html) {
    root.innerHTML = html;
    root._torqueLastHtml = html;
    changed = true;
  }
  _terminalDirectMessagesAttachPagination(root);
  _terminalDirectMessagesApplyPersistedHeight(root);
  const canRestorePrevious = previous
    && (!previous.agentId || previous.agentId === String(agent.id || ''));
  if (canRestorePrevious) {
    const restorePrevious = root._terminalDirectMessagesLoadingOlder
      ? Object.assign({}, previous, { atTail: false })
      : previous;
    _restoreTerminalDirectMessagesState(root, { terminalDirectMessages: restorePrevious });
  } else if (changed) {
    const list = _terminalDirectMessagesList(root);
    if (list && typeof list.scrollTop === 'number') {
      list.scrollTop = Math.max(0, (Number(list.scrollHeight) || 0) - (Number(list.clientHeight) || 0));
      // Opening / switching into an agent lands at the bottom: start following.
      _terminalDirectMessagesSetPinned(String(agent.id || ''), true);
    }
  }
}

function _terminalDirectMessagesList(root) {
  if (!root || typeof root.querySelector !== 'function') return null;
  return root.querySelector('.terminal-direct-messages-list');
}

function _terminalDirectMessagesSection(root) {
  if (!root || typeof root.querySelector !== 'function') return null;
  return root.querySelector('.terminal-direct-messages');
}

function _terminalDirectMessagesSetStyleVar(el, key, value) {
  if (!el || !el.style) return;
  if (typeof el.style.setProperty === 'function') el.style.setProperty(key, value);
  else el.style[key] = value;
}

function _terminalDirectMessagesRemoveStyleVar(el, key) {
  if (!el || !el.style) return;
  if (typeof el.style.removeProperty === 'function') el.style.removeProperty(key);
  else delete el.style[key];
}

function _terminalDirectMessagesHeightBounds(root) {
  const min = TERMINAL_DIRECT_MESSAGES_MIN_HEIGHT;
  let max = 0;
  const shell = root && typeof root.closest === 'function'
    ? root.closest('.terminal-shell')
    : null;
  if (shell && typeof shell.getBoundingClientRect === 'function') {
    const rect = shell.getBoundingClientRect();
    const shellHeight = rect && Number.isFinite(rect.height) ? rect.height : 0;
    if (shellHeight >= 320) max = Math.floor(shellHeight * 0.55);
  }
  if (!max && typeof window !== 'undefined' && typeof window.innerHeight === 'number') {
    max = Math.floor(window.innerHeight * 0.45);
  }
  max = Math.max(min, max || TERMINAL_DIRECT_MESSAGES_MAX_HEIGHT_FALLBACK);
  return { min: min, max: max };
}

function _terminalDirectMessagesClampHeight(root, height) {
  const raw = parseInt(height, 10);
  if (!Number.isFinite(raw) || raw <= 0) return 0;
  const bounds = _terminalDirectMessagesHeightBounds(root);
  return Math.max(bounds.min, Math.min(bounds.max, raw));
}

function _terminalDirectMessagesPersistedHeight() {
  const raw = parseInt(state ? state.terminal_direct_messages_height : 0, 10);
  return Number.isFinite(raw) && raw > 0 ? raw : 0;
}

function _terminalDirectMessagesCurrentHeight(root) {
  const section = _terminalDirectMessagesSection(root);
  if (section && typeof section.getBoundingClientRect === 'function') {
    const rect = section.getBoundingClientRect();
    if (rect && Number.isFinite(rect.height) && rect.height > 0) {
      return rect.height;
    }
  }
  if (section && Number(section.offsetHeight) > 0) return Number(section.offsetHeight);
  const saved = _terminalDirectMessagesPersistedHeight();
  return saved || TERMINAL_DIRECT_MESSAGES_DEFAULT_HEIGHT;
}

function _terminalDirectMessagesSetHeight(root, height) {
  const clamped = _terminalDirectMessagesClampHeight(root, height);
  if (!root) return clamped;
  if (root.dataset) {
    if (clamped > 0) root.dataset.resized = 'true';
    else delete root.dataset.resized;
  }
  if (clamped > 0) {
    _terminalDirectMessagesSetStyleVar(root, '--terminal-direct-messages-height', clamped + 'px');
  } else {
    _terminalDirectMessagesRemoveStyleVar(root, '--terminal-direct-messages-height');
  }
  return clamped;
}

function _terminalDirectMessagesApplyPersistedHeight(root) {
  return _terminalDirectMessagesSetHeight(root, _terminalDirectMessagesPersistedHeight());
}

function _terminalDirectMessagesSlotFromEvent(event) {
  const target = event && (event.currentTarget || event.target);
  if (target && typeof target.closest === 'function') {
    const slot = target.closest('.terminal-direct-messages-slot');
    if (slot) return slot;
  }
  const workspace = document.getElementById ? document.getElementById('terminal-workspace') : null;
  return workspace && workspace.querySelector
    ? workspace.querySelector('.terminal-direct-messages-slot')
    : null;
}

function _terminalDirectMessagesApplyResizeHeight(root, height) {
  const workspace = document.getElementById ? document.getElementById('terminal-workspace') : null;
  const cell = _resolveTerminalWorkspaceCell();
  const snapshot = workspace ? _captureTerminalWorkspaceState(workspace, cell) : null;
  const applied = _terminalDirectMessagesSetHeight(root, height);
  if (state) state.terminal_direct_messages_height = applied;
  if (workspace) _restoreTerminalWorkspaceState(workspace, snapshot, cell);
  return applied;
}

function _terminalDirectMessageStopEvent(event) {
  if (!event) return;
  if (typeof event.preventDefault === 'function') event.preventDefault();
  if (typeof event.stopPropagation === 'function') event.stopPropagation();
}

function _terminalDirectMessageMarkdownLinkTarget(event) {
  const target = event && event.target;
  if (!target || typeof target.closest !== 'function') return null;
  return target.closest('[data-torque-markdown-link]');
}

function _terminalDirectMessageRowFromEvent(event) {
  const current = event && event.currentTarget;
  if (current && current.classList && current.classList.contains('terminal-direct-message')) {
    return current;
  }
  const target = (event && event.target) || current;
  if (target && typeof target.closest === 'function') {
    return target.closest('.terminal-direct-message');
  }
  return null;
}

function _terminalDirectMessageEventPoint(event) {
  const x = event && Number(event.clientX);
  const y = event && Number(event.clientY);
  return {
    x: Number.isFinite(x) ? x : 0,
    y: Number.isFinite(y) ? y : 0,
  };
}

function _terminalDirectMessageSelectionText(row) {
  let selection = null;
  if (typeof window !== 'undefined' && window && typeof window.getSelection === 'function') {
    selection = window.getSelection();
  } else if (typeof document !== 'undefined' && document && typeof document.getSelection === 'function') {
    selection = document.getSelection();
  }
  if (!selection || typeof selection.toString !== 'function') return '';
  const text = String(selection.toString() || '');
  if (!text) return '';
  const anchor = selection.anchorNode || null;
  const focus = selection.focusNode || null;
  if (row && typeof row.contains === 'function' && (anchor || focus)) {
    if ((anchor && row.contains(anchor)) || (focus && row.contains(focus))) return text;
    return '';
  }
  return text;
}

function _terminalDirectMessageClickIsSelectionDrag(event, messageId) {
  if (!event) return false;
  if (event && (event.type === 'keydown' || event.key)) return false;
  const row = _terminalDirectMessageRowFromEvent(event);
  if (_terminalDirectMessageSelectionText(row)) return true;
  const down = _terminalDirectMessagePointerDown;
  if (!down) return false;
  const point = _terminalDirectMessageEventPoint(event);
  const dx = point.x - down.x;
  const dy = point.y - down.y;
  const distanceSq = (dx * dx) + (dy * dy);
  const threshold = Number(TERMINAL_DIRECT_MESSAGE_CLICK_DRAG_THRESHOLD_PX) || 4;
  if (distanceSq > threshold * threshold) return true;
  const elapsed = Date.now() - Number(down.time || 0);
  const duration = Number(TERMINAL_DIRECT_MESSAGE_CLICK_DRAG_DURATION_MS) || 650;
  if (elapsed > duration && distanceSq > 1) return true;
  const mid = String(messageId || '').trim();
  if (down.messageId && mid && down.messageId !== mid) return true;
  return false;
}

function terminalDirectMessageMouseDown(event) {
  if (event && typeof event.button === 'number' && event.button !== 0) return true;
  if (_terminalDirectMessageMarkdownLinkTarget(event)) {
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
    return true;
  }
  const row = _terminalDirectMessageRowFromEvent(event);
  const point = _terminalDirectMessageEventPoint(event);
  _terminalDirectMessagePointerDown = {
    x: point.x,
    y: point.y,
    time: Date.now(),
    messageId: row && row.dataset ? String(row.dataset.directMessageId || '') : '',
  };
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  return true;
}

function _terminalDirectMessageCloseContextMenu() {
  if (typeof _closeCtxMenu === 'function') {
    _closeCtxMenu();
    return;
  }
  if (typeof closeContextMenu === 'function') {
    closeContextMenu();
    return;
  }
  const menu = (typeof document !== 'undefined' && document && document.getElementById)
    ? document.getElementById('ctx-menu')
    : null;
  if (menu && menu.classList) menu.classList.remove('open');
}

function _terminalDirectMessageAdjustContextMenu(menu) {
  if (!menu) return;
  if (typeof _adjustCtxMenuOverflow === 'function') {
    _adjustCtxMenuOverflow();
    return;
  }
  const adjust = function() {
    if (!menu || typeof menu.getBoundingClientRect !== 'function') return;
    const rect = menu.getBoundingClientRect();
    const viewportWidth = (typeof window !== 'undefined' && window) ? Number(window.innerWidth || 0) : 0;
    const viewportHeight = (typeof window !== 'undefined' && window) ? Number(window.innerHeight || 0) : 0;
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

function terminalDirectMessageContextMenu(event, agentId, messageId) {
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  const aid = String(agentId || '').trim();
  const mid = String(messageId || '').trim();
  const menu = (typeof document !== 'undefined' && document && document.getElementById)
    ? document.getElementById('ctx-menu')
    : null;
  if (!aid || !mid || !menu) return false;
  const action = 'terminalDirectMessageCopy(' + JSON.stringify(aid) + ',' + JSON.stringify(mid) + ')';
  menu.innerHTML = '<button onclick="event.stopPropagation();' + esc(action) + '">Copy</button>';
  menu.style.top = ((event && Number.isFinite(Number(event.clientY))) ? Number(event.clientY) : 0) + 'px';
  const x = (event && Number.isFinite(Number(event.clientX))) ? Number(event.clientX) : 0;
  const viewportWidth = (typeof window !== 'undefined' && window) ? Number(window.innerWidth || 0) : 0;
  menu.style.left = Math.max(0, viewportWidth ? Math.min(x, viewportWidth - 140) : x) + 'px';
  if (menu.classList) menu.classList.add('open');
  _terminalDirectMessageAdjustContextMenu(menu);
  return false;
}

function terminalDirectMessageCopy(agentId, messageId) {
  const row = _terminalDirectMessageById(agentId, messageId);
  const text = _terminalDirectMessageText(row);
  const clipboard = (typeof navigator !== 'undefined' && navigator) ? navigator.clipboard : null;
  const close = function() { _terminalDirectMessageCloseContextMenu(); };
  if (clipboard && typeof clipboard.writeText === 'function') {
    let result = null;
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

function _terminalDirectMessageFocusIsTerminal(root) {
  if (!root || typeof document === 'undefined') return false;
  const active = document.activeElement;
  if (!active) return false;
  if (active.classList && active.classList.contains('terminal-compose-input')) return false;
  if (typeof active.closest === 'function') {
    if (active.closest('.terminal-compose')) return false;
    if (active.closest('.terminal-stage') || active.closest('.terminal-surface')) return true;
  }
  const stage = root.querySelector ? root.querySelector('.terminal-stage') : null;
  if (stage && typeof stage.contains === 'function' && stage.contains(active)) return true;
  const surface = root.querySelector ? root.querySelector('.terminal-surface') : null;
  return !!(surface && typeof surface.contains === 'function' && surface.contains(active));
}

function _captureTerminalDirectMessageInteractionState(root, cell) {
  const snapshot = root ? _captureTerminalWorkspaceState(root, cell) : null;
  if (snapshot && snapshot.focus && _terminalDirectMessageFocusIsTerminal(root)) {
    snapshot.focus = null;
  }
  return snapshot;
}

function terminalDirectMessagesResizeStart(event) {
  if (!event || (typeof event.button === 'number' && event.button !== 0)) return false;
  const root = _terminalDirectMessagesSlotFromEvent(event);
  if (!root || root.hidden) return false;
  if (typeof event.preventDefault === 'function') event.preventDefault();
  if (typeof event.stopPropagation === 'function') event.stopPropagation();
  _terminalDirectMessagesResizeDrag = {
    root: root,
    startY: Number.isFinite(event.clientY) ? event.clientY : 0,
    startHeight: _terminalDirectMessagesCurrentHeight(root),
    currentHeight: _terminalDirectMessagesCurrentHeight(root),
    changed: false,
  };
  if (document && document.body) {
    if (document.body.classList) document.body.classList.add('terminal-direct-messages-resizing');
    if (document.body.style) document.body.style.cursor = 'ns-resize';
  }
  document.addEventListener('mousemove', _terminalDirectMessagesResizeMove);
  document.addEventListener('mouseup', _terminalDirectMessagesResizeEnd);
  return false;
}

function _terminalDirectMessagesResizeMove(event) {
  const drag = _terminalDirectMessagesResizeDrag;
  if (!drag) return;
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  const clientY = event && Number.isFinite(event.clientY) ? event.clientY : drag.startY;
  const next = drag.startHeight - (clientY - drag.startY);
  drag.currentHeight = _terminalDirectMessagesApplyResizeHeight(drag.root, next);
  drag.changed = true;
}

function _terminalDirectMessagesClearResizeDrag() {
  document.removeEventListener('mousemove', _terminalDirectMessagesResizeMove);
  document.removeEventListener('mouseup', _terminalDirectMessagesResizeEnd);
  if (document && document.body) {
    if (document.body.classList) document.body.classList.remove('terminal-direct-messages-resizing');
    if (document.body.style) document.body.style.cursor = '';
  }
  _terminalDirectMessagesResizeDrag = null;
}

function _terminalDirectMessagesPersistHeight(height, root) {
  const applied = _terminalDirectMessagesClampHeight(root || null, height);
  if (state) state.terminal_direct_messages_height = applied;
  if (typeof send === 'function') {
    send({ cmd: 'ui_set_terminal_direct_messages_height', height: applied });
  }
}

function _terminalDirectMessagesResizeEnd(event) {
  const drag = _terminalDirectMessagesResizeDrag;
  if (!drag) return;
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  if (event && Number.isFinite(event.clientY)) {
    const next = drag.startHeight - (event.clientY - drag.startY);
    drag.currentHeight = _terminalDirectMessagesApplyResizeHeight(drag.root, next);
    if (event.clientY !== drag.startY) drag.changed = true;
  }
  const shouldPersist = !!drag.changed;
  const height = drag.currentHeight;
  _terminalDirectMessagesClearResizeDrag();
  if (shouldPersist) _terminalDirectMessagesPersistHeight(height, drag.root);
}

function terminalDirectMessagesResizeKeydown(event) {
  const key = event && (event.key || event.code);
  const deltas = {
    ArrowUp: 18,
    Up: 18,
    ArrowDown: -18,
    Down: -18,
    PageUp: 72,
    PageDown: -72,
  };
  if (!Object.prototype.hasOwnProperty.call(deltas, key)) return false;
  const root = _terminalDirectMessagesSlotFromEvent(event);
  if (!root || root.hidden) return false;
  if (typeof event.preventDefault === 'function') event.preventDefault();
  if (typeof event.stopPropagation === 'function') event.stopPropagation();
  const current = _terminalDirectMessagesCurrentHeight(root);
  const applied = _terminalDirectMessagesApplyResizeHeight(root, current + deltas[key]);
  _terminalDirectMessagesPersistHeight(applied, root);
  return false;
}

function _terminalDirectMessageAnchorItems(list) {
  if (!list || typeof list.querySelectorAll !== 'function') return [];
  return Array.prototype.slice.call(list.querySelectorAll('[data-terminal-dm-anchor]') || []);
}

function _terminalDirectMessagesAtTail(list) {
  if (!list) return true;
  const scrollTop = Number(list.scrollTop) || 0;
  const clientHeight = Number(list.clientHeight) || 0;
  const scrollHeight = Number(list.scrollHeight) || 0;
  return scrollHeight <= clientHeight
    || (scrollHeight - scrollTop - clientHeight) <= TERMINAL_DIRECT_MESSAGES_TAIL_THRESHOLD_PX;
}

// Returns the sticky follow-tail intent for an agent, or null when the user has
// not interacted yet (caller falls back to a live at-tail measurement).
function _terminalDirectMessagesStoredPinned(agentId) {
  const id = String(agentId || '');
  if (id && Object.prototype.hasOwnProperty.call(_terminalDirectMessagePinnedToTailByAgent, id)) {
    return !!_terminalDirectMessagePinnedToTailByAgent[id];
  }
  return null;
}

function _terminalDirectMessagesSetPinned(agentId, pinned) {
  const id = String(agentId || '');
  if (!id) return;
  _terminalDirectMessagePinnedToTailByAgent[id] = !!pinned;
}

function _terminalDirectMessagesLoadOlder(root) {
  if (!root) return false;
  if (root._terminalDirectMessagesSuppressOlderLoad || root._terminalDirectMessagesLoadingOlder) return false;
  const list = _terminalDirectMessagesList(root);
  const agentId = String((list && list.dataset && list.dataset.agentId)
    || (root.dataset && root.dataset.agentId)
    || '').trim();
  if (!agentId) return false;
  const rows = _terminalDirectMessagesForAgent(agentId);
  const current = _terminalDirectMessagesVisibleCount(agentId, rows.length);
  if (current >= rows.length) return false;
  const agent = state && state.agents ? state.agents[agentId] : null;
  if (!agent) return false;
  const next = Math.min(rows.length, current + _terminalDirectMessagesWindowSize());
  _terminalDirectMessagesSetVisibleCount(agentId, next, rows.length);
  root._terminalDirectMessagesSuppressOlderLoad = true;
  root._terminalDirectMessagesLoadingOlder = true;
  try {
    _renderTerminalDirectMessages(root, agent);
  } finally {
    root._terminalDirectMessagesLoadingOlder = false;
    if (typeof setTimeout === 'function') {
      setTimeout(function() { root._terminalDirectMessagesSuppressOlderLoad = false; }, 100);
    } else {
      root._terminalDirectMessagesSuppressOlderLoad = false;
    }
  }
  return true;
}

function terminalDirectMessagesScroll(event) {
  const list = event && (event.currentTarget || event.target);
  if (!list || typeof list.scrollTop !== 'number') return;
  // Track follow-tail intent on every scroll (user wheel or programmatic). A
  // deliberate scroll-up past the tail threshold detaches; scrolling back to the
  // bottom re-attaches. Re-pins after a render leave us at the bottom, so the
  // flag stays true and the panel keeps following new messages.
  const agentId = String((list.dataset && list.dataset.agentId) || '');
  if (agentId) _terminalDirectMessagesSetPinned(agentId, _terminalDirectMessagesAtTail(list));
  if (Number(list.scrollTop || 0) > TERMINAL_DIRECT_MESSAGES_SCROLL_TOP_THRESHOLD) return;
  const root = list._terminalDirectMessagesRoot
    || (typeof list.closest === 'function' ? list.closest('.terminal-direct-messages-slot') : null);
  _terminalDirectMessagesLoadOlder(root);
}

function _terminalDirectMessagesAttachPagination(root) {
  const list = _terminalDirectMessagesList(root);
  if (!list) return;
  list._terminalDirectMessagesRoot = root;
  if (list._terminalDirectMessagesPaginationAttached) return;
  list._terminalDirectMessagesPaginationAttached = true;
  if (typeof list.addEventListener === 'function') {
    list.addEventListener('scroll', terminalDirectMessagesScroll);
  }
}

function _captureTerminalDirectMessagesState(root) {
  const list = _terminalDirectMessagesList(root);
  if (!list) return null;
  const scrollTop = Number(list.scrollTop) || 0;
  const items = _terminalDirectMessageAnchorItems(list);
  let anchorId = '';
  let anchorOffset = 0;
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (!item || !item.dataset) continue;
    const bottom = (Number(item.offsetTop) || 0) + (Number(item.offsetHeight) || 0);
    if (bottom >= scrollTop) {
      anchorId = String(item.dataset.terminalDmAnchor || '');
      anchorOffset = (Number(item.offsetTop) || 0) - scrollTop;
      break;
    }
  }
  const agentId = String((list.dataset && list.dataset.agentId) || '');
  // Prefer the sticky follow-tail intent. Recomputing at-tail from the DOM here
  // is fragile: a render that catches the viewport a few px off the bottom would
  // flip to anchor-freeze and never re-pin, so the latest messages keep landing
  // below view. Fall back to a live measurement only before the user has scrolled.
  const storedPinned = _terminalDirectMessagesStoredPinned(agentId);
  const atTail = storedPinned === null ? _terminalDirectMessagesAtTail(list) : storedPinned;
  return {
    agentId: agentId,
    atTail: atTail,
    scrollTop: scrollTop,
    anchorId: anchorId,
    anchorOffset: anchorOffset,
  };
}

function _restoreTerminalDirectMessagesState(root, snapshot) {
  if (!snapshot || !snapshot.terminalDirectMessages) return;
  const saved = snapshot.terminalDirectMessages;
  const list = _terminalDirectMessagesList(root);
  if (!list) return;
  if (saved.agentId && list.dataset && String(list.dataset.agentId || '') !== saved.agentId) return;
  if (saved.atTail) {
    list.scrollTop = Math.max(0, (Number(list.scrollHeight) || 0) - (Number(list.clientHeight) || 0));
    _terminalDirectMessagesSetPinned(saved.agentId || (list.dataset && list.dataset.agentId), true);
    return;
  }
  const items = _terminalDirectMessageAnchorItems(list);
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (item && item.dataset && String(item.dataset.terminalDmAnchor || '') === saved.anchorId) {
      list.scrollTop = Math.max(0, (Number(item.offsetTop) || 0) - (Number(saved.anchorOffset) || 0));
      return;
    }
  }
  if (typeof saved.scrollTop === 'number') list.scrollTop = saved.scrollTop;
}

function terminalDirectMessageSelect(agentId, messageId) {
  let event = null;
  if (agentId && typeof agentId === 'object') {
    event = agentId;
    agentId = messageId;
    messageId = arguments.length > 2 ? arguments[2] : '';
  }
  if (_terminalDirectMessageMarkdownLinkTarget(event)) {
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
    _terminalDirectMessagePointerDown = null;
    return true;
  }
  if (_terminalDirectMessageClickIsSelectionDrag(event, messageId)) {
    if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
    _terminalDirectMessagePointerDown = null;
    return true;
  }
  _terminalDirectMessagePointerDown = null;
  _terminalDirectMessageStopEvent(event);
  const aid = String(agentId || '').trim();
  const mid = String(messageId || '').trim();
  if (!aid || !mid) return false;
  const root = document.getElementById ? document.getElementById('terminal-workspace') : null;
  const cell = _resolveTerminalWorkspaceCell();
  const snapshot = _captureTerminalDirectMessageInteractionState(root, cell);
  _terminalDirectMessageSelectedByAgent[aid] = mid;
  const slot = root && root.querySelector ? root.querySelector('.terminal-direct-messages-slot') : null;
  if (slot) _renderTerminalDirectMessages(slot, cell);
  if (root) _restoreTerminalWorkspaceState(root, snapshot, cell);
  return false;
}

function terminalDirectMessageKeydown(evt, agentId, messageId) {
  if (!evt) return;
  if (_terminalDirectMessageMarkdownLinkTarget(evt)) {
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    return;
  }
  if (evt.key !== 'Enter' && evt.key !== ' ') return;
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
  terminalDirectMessageSelect(evt, agentId, messageId);
}

function terminalDirectMessageReply(evt, agentId, messageId) {
  if (_terminalDirectMessageClickIsSelectionDrag(evt, messageId)) {
    if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
    _terminalDirectMessagePointerDown = null;
    return true;
  }
  _terminalDirectMessagePointerDown = null;
  _terminalDirectMessageStopEvent(evt);
  const aid = String(agentId || '').trim();
  const mid = String(messageId || '').trim();
  if (!aid || !mid) return false;
  const root = document.getElementById ? document.getElementById('terminal-workspace') : null;
  const cell = _resolveTerminalWorkspaceCell();
  const snapshot = _captureTerminalDirectMessageInteractionState(root, cell);
  _terminalDirectMessageSelectedByAgent[aid] = mid;
  _terminalDirectMessageReplyToByAgent[aid] = mid;
  if (typeof renderTerminalWorkspace === 'function') {
    renderTerminalWorkspace({ suppressTerminalFocus: true });
  }
  if (root) _restoreTerminalWorkspaceState(root, snapshot, cell);
  const input = root && root.querySelector ? root.querySelector('.terminal-compose-input') : null;
  if (input && typeof input.focus === 'function') input.focus();
  return false;
}

function terminalDirectMessageCancelReply(evt, agentId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  const aid = String(agentId || '').trim();
  if (aid) delete _terminalDirectMessageReplyToByAgent[aid];
  if (typeof renderTerminalWorkspace === 'function') renderTerminalWorkspace();
  return false;
}

function _terminalGroupCells(group) {
  const out = [];
  if (!group || !state || !state.groups || !state.agents) return out;
  // `state.groups[group]` already contains every cell in the group —
  // agents and their child terminals. The previous loop then also
  // expanded each agent's `state.children`, so every child terminal was
  // pushed twice, producing duplicate tabs in the embedded workspace
  // top bar. Dedupe by id.
  const seen = Object.create(null);
  const ids = state.groups[group] || [];
  for (let i = 0; i < ids.length; i++) {
    const id = ids[i];
    if (seen[id]) continue;
    const cell = state.agents[id];
    if (!cell || _terminalCellIsTombstoned(cell)) continue;
    seen[id] = true;
    out.push(cell);
    if (cell.cell_type === 'agent') {
      const kids = state.children && state.children[cell.id] ? state.children[cell.id] : [];
      for (let j = 0; j < kids.length; j++) {
        const childId = kids[j];
        if (seen[childId]) continue;
        const child = state.agents[childId];
        if (!child || _terminalCellIsTombstoned(child)) continue;
        seen[childId] = true;
        out.push(child);
      }
    }
  }
  return out;
}

function _resolveTerminalWorkspaceCell() {
  if (!state || !state.agents) return null;
  const activeGroup = (typeof _singleGroupModeEnabled === 'function'
    && _singleGroupModeEnabled()
    && typeof _activeGroup === 'function')
    ? (_activeGroup() || '')
    : '';
  if (selectedTerminalId && state.agents[selectedTerminalId]) {
    const selected = state.agents[selectedTerminalId];
    if (!activeGroup || selected.group === activeGroup) return selected;
  }
  if (state.active_session_id) {
    for (const id in state.agents) {
      const cell = state.agents[id];
      if (cell.session_id === state.active_session_id) {
        if (activeGroup && cell.group !== activeGroup) continue;
        selectedTerminalId = id;
        return cell;
      }
    }
  }
  if (selectedAgentId && state.agents[selectedAgentId]) {
    const selectedAgent = state.agents[selectedAgentId];
    if (!activeGroup || selectedAgent.group === activeGroup) {
      selectedTerminalId = selectedAgentId;
      return selectedAgent;
    }
  }
  const group = _terminalCurrentGroupName();
  const cells = _terminalGroupCells(group);
  if (cells.length) {
    selectedTerminalId = cells[0].id;
    return cells[0];
  }
  const ids = Object.keys(state.agents).filter(function(id) {
    return !activeGroup || (state.agents[id] && state.agents[id].group === activeGroup);
  });
  if (!ids.length) return null;
  selectedTerminalId = ids[0];
  return state.agents[ids[0]];
}

function _terminalStatusLabel(cell) {
  if (!cell) return 'No terminal selected';
  if (cell.needs_attention) return cell.error_message || 'Needs attention';
  if (cell.status === 'stopped') return 'Stopped';
  if (cell.agent_type && cell.activity_detail) return cell.activity_detail;
  if (cell.agent_type && cell.activity) return cell.activity;
  if (cell.current_process) return cell.current_process;
  return cell.status || 'idle';
}

function _terminalDisplayPath(cell) {
  if (!cell) return '';
  const fullPath = cell.current_path || cell.directory || '';
  return _formatDisplayPath(
    fullPath,
    cell.git_root || cell.worktree_repo_root || ''
  );
}

function _terminalPrimaryAction(groupLabel, agentTarget) {
  return null;
}

function _embeddedTerminalCanTakeFocus(force) {
  if (!_embeddedTerminal || !isEmbeddedTerminalMode()) return false;
  if (!_embeddedTerminalSessionKey) return false;
  if (!force && _embeddedTerminalPendingFocusKey !== _embeddedTerminalSessionKey) {
    return false;
  }
  if (document.querySelector && document.querySelector('.overlay.visible')) return false;
  const active = document.activeElement;
  if (!active || active === document.body) return true;
  const workspace = document.getElementById('terminal-workspace');
  if (workspace && typeof workspace.contains === 'function' && workspace.contains(active)) {
    return true;
  }
  if (force) return true;
  const tag = (active.tagName || '').toUpperCase();
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || active.isContentEditable) {
    return false;
  }
  return true;
}

function focusEmbeddedTerminalWorkspace(force) {
  if (!_embeddedTerminalCanTakeFocus(!!force)) return false;
  const expectedKey = _embeddedTerminalSessionKey;
  requestAnimationFrame(function() {
    if (_embeddedTerminalSessionKey !== expectedKey) return;
    if (!_embeddedTerminalCanTakeFocus(!!force)) return;
    if (typeof _embeddedTerminal.focus === 'function') _embeddedTerminal.focus();
    _embeddedTerminalPendingFocusKey = '';
  });
  return true;
}

function _ensureTerminalWorkspaceDom(root) {
  let shell = root.querySelector('.terminal-shell');
  if (!shell) {
    root.innerHTML = ''
      + '<div class="terminal-shell">'
      + '  <div class="terminal-topbar"></div>'
      + '  <div class="terminal-stage"></div>'
      + '  <div class="terminal-direct-messages-slot"></div>'
      + '  <div class="terminal-compose-slot"></div>'
      + '  <div class="terminal-statusbar"></div>'
      + '</div>';
    shell = root.querySelector('.terminal-shell');
  }
  const legacyTabs = shell.querySelector('.terminal-tabs');
  if (legacyTabs) {
    legacyTabs.innerHTML = '';
    legacyTabs._torqueLastHtml = '';
    if (legacyTabs.classList && typeof legacyTabs.classList.add === 'function') {
      legacyTabs.classList.add('terminal-tabs-hidden');
    }
    if (legacyTabs.parentNode && typeof legacyTabs.remove === 'function') {
      legacyTabs.remove();
    }
  }
  let directMessages = shell.querySelector('.terminal-direct-messages-slot');
  if (!directMessages && document.createElement) {
    directMessages = document.createElement('div');
    directMessages.className = 'terminal-direct-messages-slot';
    const composeSlot = shell.querySelector('.terminal-compose-slot');
    const stage = shell.querySelector('.terminal-stage');
    if (composeSlot && typeof shell.insertBefore === 'function') {
      shell.insertBefore(directMessages, composeSlot);
    } else if (stage && stage.nextElementSibling && typeof shell.insertBefore === 'function') {
      shell.insertBefore(directMessages, stage.nextElementSibling);
    } else if (typeof shell.appendChild === 'function') {
      shell.appendChild(directMessages);
    }
  }
  let compose = shell.querySelector('.terminal-compose-slot');
  if (!compose && document.createElement) {
    compose = document.createElement('div');
    compose.className = 'terminal-compose-slot';
    const statusbar = shell.querySelector('.terminal-statusbar');
    if (statusbar && typeof shell.insertBefore === 'function') {
      shell.insertBefore(compose, statusbar);
    } else if (typeof shell.appendChild === 'function') {
      shell.appendChild(compose);
    }
  }
  return {
    shell: shell,
    topbar: shell.querySelector('.terminal-topbar'),
    tabs: null,
    stage: shell.querySelector('.terminal-stage'),
    directMessages: directMessages,
    compose: compose,
    statusbar: shell.querySelector('.terminal-statusbar'),
  };
}

function _terminalCellIsTombstoned(cell) {
  const value = Number((cell && cell.deleted_at) || 0);
  return Number.isFinite(value) && value > 0;
}

function _terminalSelectionBelongsToClosedCell(selectedId, cellId) {
  if (!selectedId) return false;
  if (String(selectedId) === String(cellId)) return true;
  const selectedCell = state && state.agents ? state.agents[selectedId] : null;
  return !!(selectedCell && String(selectedCell.parent_id || '') === String(cellId));
}

function _terminalClearSelectionForClosedCell(cellId) {
  if (_terminalSelectionBelongsToClosedCell(selectedAgentId, cellId)) selectedAgentId = null;
  if (typeof selectedTerminalId !== 'undefined'
      && _terminalSelectionBelongsToClosedCell(selectedTerminalId, cellId)) {
    selectedTerminalId = null;
  }
  if (typeof focusedItemId !== 'undefined'
      && _terminalSelectionBelongsToClosedCell(focusedItemId, cellId)) {
    focusedItemId = null;
  }
}

function closeTerminalTab(cellId, evt) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  const cell = state && state.agents ? state.agents[cellId] : null;
  if (!cell) return false;
  if (_terminalCellIsTombstoned(cell)) {
    _terminalClearSelectionForClosedCell(cellId);
    send({ cmd: 'purge_agent_now', id: cellId });
    return false;
  }
  if (typeof removeAgent === 'function') removeAgent(cellId);
  return false;
}

function _renderTerminalTabs(cells, activeId) {
  return '';
}

function _terminalShouldShowTabs(cells) {
  return false;
}

function _terminalComposeDomId(cellId) {
  const safe = String(cellId || '')
    .replace(/[^A-Za-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return safe || 'selected';
}

function _terminalComposeInputId(cellId) {
  return 'terminal-compose-input-' + _terminalComposeDomId(cellId);
}

function _terminalComposeButtonId(cellId) {
  return 'terminal-compose-submit-' + _terminalComposeDomId(cellId);
}

function _terminalComposeHistoryButtonId(cellId) {
  return 'terminal-compose-history-' + _terminalComposeDomId(cellId);
}

function _terminalComposeHistoryMenuId(cellId) {
  return 'terminal-compose-history-menu-' + _terminalComposeDomId(cellId);
}

function _terminalComposeTaskDropdownId(cellId) {
  return 'terminal-compose-task-dropdown-' + _terminalComposeDomId(cellId);
}

function _terminalComposeContainerFor(el) {
  for (let node = el; node; node = node.parentNode) {
    if (node.classList && node.classList.contains('terminal-compose')) {
      return node;
    }
  }
  return null;
}

function _terminalComposeTextarea(root) {
  return root && root.querySelector ? root.querySelector('.terminal-compose-input') : null;
}

function _terminalComposeButtonFor(el, cellId) {
  const container = _terminalComposeContainerFor(el);
  if (container && container.querySelector) {
    const btn = container.querySelector('.terminal-compose-submit');
    if (btn) return btn;
  }
  const id = _terminalComposeButtonId(cellId || (el && el.dataset ? el.dataset.cellId : ''));
  return document.getElementById ? document.getElementById(id) : null;
}

function _terminalComposeAutoResize(el) {
  if (!el) return;
  if (typeof taskAutoResize === 'function') {
    taskAutoResize(el);
  } else if (typeof boardAddTaskAutoResize === 'function') {
    boardAddTaskAutoResize(el);
  }
}

function _terminalComposeSetButtonState(input) {
  if (!input) return;
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  const button = _terminalComposeButtonFor(input, cellId);
  if (button) button.disabled = !String(input.value || '').trim();
}

function _terminalMessageHistoryEntries(cellId) {
  const id = String(cellId || '');
  const history = state && state.agent_message_history
    ? state.agent_message_history[id]
    : null;
  if (!Array.isArray(history)) return [];
  return history.filter(function(entry) {
    return entry && typeof entry.message === 'string' && entry.message.length;
  });
}

function _terminalComposeHistoryButtonFor(cellId) {
  return document.getElementById
    ? document.getElementById(_terminalComposeHistoryButtonId(cellId))
    : null;
}

function _terminalComposeHistoryMenuFor(cellId) {
  return document.getElementById
    ? document.getElementById(_terminalComposeHistoryMenuId(cellId))
    : null;
}

function _terminalComposeHistoryPreview(message) {
  const text = String(message || '').replace(/\s+/g, ' ').trim();
  if (text.length <= 160) return text || '(empty message)';
  return text.slice(0, 157) + '\u2026';
}

function _terminalComposeHistoryRenderMenu(cellId) {
  const id = String(cellId || '');
  const menu = _terminalComposeHistoryMenuFor(id);
  if (!menu) return;
  const entries = _terminalMessageHistoryEntries(id).slice(0, 12);
  let html = ''
    + '<div class="terminal-compose-history-title">Recent messages</div>';
  if (!entries.length) {
    html += '<div class="terminal-compose-history-empty">'
      + 'No sent messages yet.'
      + '</div>';
  } else {
    html += '<div class="terminal-compose-history-list">';
    for (let i = 0; i < entries.length; i++) {
      const preview = _terminalComposeHistoryPreview(entries[i].message);
      html += '<button type="button" class="terminal-compose-history-item"'
        + ' role="option" data-cell-id="' + esc(id) + '" data-history-index="' + i + '"'
        + ' onclick="return terminalComposeHistoryPick(event)"'
        + ' title="' + esc(preview) + '">'
        + esc(preview)
        + '</button>';
    }
    html += '</div>';
  }
  html += '<div class="terminal-compose-history-hint">\u2191/\u2193 also recall history</div>';
  menu.innerHTML = html;
}

function _terminalComposeHistoryIsOpen(cellId) {
  const id = String(cellId || '');
  const menu = _terminalComposeHistoryMenuFor(id);
  return !!(id && _terminalComposeHistoryOpenCellId === id && menu && menu.hidden !== true);
}

function _terminalComposeHistoryClose(cellId, focusButton) {
  const id = String(cellId || _terminalComposeHistoryOpenCellId || '');
  if (!id) return;
  const menu = _terminalComposeHistoryMenuFor(id);
  if (menu) menu.hidden = true;
  const button = _terminalComposeHistoryButtonFor(id);
  if (button && typeof button.setAttribute === 'function') {
    button.setAttribute('aria-expanded', 'false');
  }
  if (_terminalComposeHistoryOpenCellId === id) _terminalComposeHistoryOpenCellId = '';
  if (focusButton && button && typeof button.focus === 'function') button.focus();
}

function _terminalComposeHistoryOpen(cellId) {
  const id = String(cellId || '');
  if (!id) return;
  if (_terminalComposeHistoryOpenCellId && _terminalComposeHistoryOpenCellId !== id) {
    _terminalComposeHistoryClose(_terminalComposeHistoryOpenCellId);
  }
  _terminalComposeHistoryRenderMenu(id);
  const menu = _terminalComposeHistoryMenuFor(id);
  if (!menu) return;
  menu.hidden = false;
  const button = _terminalComposeHistoryButtonFor(id);
  if (button && typeof button.setAttribute === 'function') {
    button.setAttribute('aria-expanded', 'true');
  }
  _terminalComposeHistoryOpenCellId = id;
}

function _terminalComposeHistoryHandleDocumentClick(evt) {
  const id = _terminalComposeHistoryOpenCellId;
  if (!id) return;
  const target = evt ? evt.target : null;
  const menu = _terminalComposeHistoryMenuFor(id);
  const button = _terminalComposeHistoryButtonFor(id);
  if (target && (
      (menu && typeof menu.contains === 'function' && menu.contains(target))
      || (button && (button === target
        || (typeof button.contains === 'function' && button.contains(target)))))) {
    return;
  }
  _terminalComposeHistoryClose(id);
}

function _terminalComposeHistoryHandleDocumentKeydown(evt) {
  if (!_terminalComposeHistoryOpenCellId || !evt || evt.key !== 'Escape') return;
  _terminalComposeHistoryClose(_terminalComposeHistoryOpenCellId, true);
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
}

if (typeof document !== 'undefined' && document.addEventListener) {
  document.addEventListener('click', _terminalComposeHistoryHandleDocumentClick);
  document.addEventListener('keydown', _terminalComposeHistoryHandleDocumentKeydown, true);
}

function terminalComposeHistoryToggle(evt, cellId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  const id = String(cellId || '');
  if (!id) return false;
  if (_terminalComposeHistoryIsOpen(id)) {
    _terminalComposeHistoryClose(id);
  } else {
    _terminalComposeHistoryOpen(id);
  }
  return false;
}

function terminalComposeHistoryPick(evt, cellId, index) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  const target = evt && evt.currentTarget ? evt.currentTarget : null;
  const id = String(cellId || (target && target.dataset ? target.dataset.cellId : '') || '');
  const entries = _terminalMessageHistoryEntries(id);
  const rawIndex = index != null
    ? index
    : (target && target.dataset ? target.dataset.historyIndex : 0);
  const idx = Math.max(0, Math.min(entries.length - 1, Number(rawIndex) || 0));
  const entry = entries[idx];
  if (!id || !entry) {
    _terminalComposeHistoryClose(id);
    return false;
  }
  const input = document.getElementById
    ? document.getElementById(_terminalComposeInputId(id))
    : null;
  if (input) {
    const recall = _terminalComposeRecallState(id);
    recall.draft = String(input.value || '');
    recall.index = idx;
    _terminalComposeSetValue(input, id, entry.message, { preserveAttachments: true });
    if (typeof input.focus === 'function') input.focus();
  }
  _terminalComposeHistoryClose(id);
  return false;
}

function _terminalComposeTaskDropdownFor(cellId) {
  return document.getElementById
    ? document.getElementById(_terminalComposeTaskDropdownId(cellId))
    : null;
}

function _terminalComposeTaskTrigger(input) {
  if (!input || typeof input.value !== 'string') return null;
  var start = typeof input.selectionStart === 'number'
    ? input.selectionStart
    : input.value.length;
  var end = typeof input.selectionEnd === 'number' ? input.selectionEnd : start;
  if (start !== end) return null;
  start = Math.max(0, Math.min(input.value.length, start));
  var before = input.value.slice(0, start);
  var match = before.match(/(^|\s):([\w:-]*)$/);
  if (!match) return null;
  return {
    start: before.length - match[2].length - 1,
    end: start,
    query: String(match[2] || '').toLowerCase(),
  };
}

function _terminalComposeTaskTitle(task) {
  return String((task && (task.task || task.title)) || '').trim();
}

function _terminalComposeTaskCandidates(query) {
  var needle = String(query || '').trim().toLowerCase();
  var tasks = state && state.board_tasks ? state.board_tasks : {};
  var matches = [];
  for (var id in tasks) {
    var task = tasks[id];
    if (!task || String(task.archived_at || '').trim()
        || String(task.lane || '') === 'Archived') {
      continue;
    }
    var taskId = String(task.id || id || '').trim();
    if (!taskId) continue;
    var title = _terminalComposeTaskTitle(task);
    var search = (taskId + ' ' + title).toLowerCase();
    if (!needle || search.indexOf(needle) >= 0) {
      matches.push({ id: taskId, title: title });
      if (matches.length >= 8) break;
    }
  }
  return matches;
}

function _terminalComposeTaskDropdownHide(cellId) {
  var id = String(cellId || _terminalComposeTaskDropdownCellId || '');
  var dropdown = id ? _terminalComposeTaskDropdownFor(id) : null;
  if (dropdown) dropdown.style.display = 'none';
  if (!id || _terminalComposeTaskDropdownCellId === id) {
    _terminalComposeTaskDropdownCellId = '';
    _terminalComposeTaskDropdownIdx = -1;
    _terminalComposeTaskDropdownResults = [];
  }
}

function _terminalComposeTaskDropdownVisible(cellId) {
  var id = String(cellId || '');
  var dropdown = id ? _terminalComposeTaskDropdownFor(id) : null;
  return !!(id
    && _terminalComposeTaskDropdownCellId === id
    && dropdown
    && dropdown.style.display !== 'none');
}

function _terminalComposeHighlightTaskOpt(opts) {
  for (var i = 0; i < opts.length; i++) {
    opts[i].classList.toggle('active', i === _terminalComposeTaskDropdownIdx);
  }
}

function _terminalComposeUpdateTaskDropdown(input) {
  if (!input) return;
  var cellId = input.dataset ? (input.dataset.cellId || '') : '';
  var dropdown = cellId ? _terminalComposeTaskDropdownFor(cellId) : null;
  if (!dropdown) return;
  var trigger = _terminalComposeTaskTrigger(input);
  if (!trigger) {
    _terminalComposeTaskDropdownHide(cellId);
    return;
  }
  var results = _terminalComposeTaskCandidates(trigger.query);
  if (!results.length) {
    _terminalComposeTaskDropdownHide(cellId);
    return;
  }
  _terminalComposeTaskDropdownCellId = cellId;
  _terminalComposeTaskDropdownIdx = -1;
  _terminalComposeTaskDropdownResults = results;
  var html = '';
  for (var i = 0; i < results.length; i++) {
    var taskId = String(results[i].id || '');
    var title = String(results[i].title || '');
    var jsTaskId = esc(taskId).replace(/'/g, "\\'");
    html += '<div class="deps-option terminal-compose-task-option"'
      + ' role="option" data-task-id="' + esc(taskId) + '"'
      + ' onmousedown="return terminalComposePickTaskRef(event, \''
      + esc(cellId).replace(/'/g, "\\'") + '\', \'' + jsTaskId + '\')">'
      + '<span class="terminal-compose-task-ref-id">' + esc(taskId) + '</span>'
      + (title
        ? '<span class="terminal-compose-task-ref-title">' + esc(title) + '</span>'
        : '')
      + '</div>';
  }
  dropdown.innerHTML = html;
  dropdown.style.display = '';
}

function _terminalComposeTaskDropdownHandleKey(evt, cellId) {
  var id = String(cellId || '');
  if (!_terminalComposeTaskDropdownVisible(id)) return false;
  var count = _terminalComposeTaskDropdownResults.length;
  if (!count) {
    _terminalComposeTaskDropdownHide(id);
    return false;
  }
  var dropdown = _terminalComposeTaskDropdownFor(id);
  var opts = dropdown && dropdown.querySelectorAll
    ? dropdown.querySelectorAll('.deps-option')
    : [];
  if (evt.key === 'ArrowDown') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    _terminalComposeTaskDropdownIdx = (_terminalComposeTaskDropdownIdx + 1) % count;
    _terminalComposeHighlightTaskOpt(opts);
    return true;
  }
  if (evt.key === 'ArrowUp') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    _terminalComposeTaskDropdownIdx = _terminalComposeTaskDropdownIdx <= 0
      ? count - 1
      : _terminalComposeTaskDropdownIdx - 1;
    _terminalComposeHighlightTaskOpt(opts);
    return true;
  }
  if (evt.key === 'Enter') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    var idx = _terminalComposeTaskDropdownIdx >= 0 ? _terminalComposeTaskDropdownIdx : 0;
    var result = _terminalComposeTaskDropdownResults[idx];
    if (result) terminalComposePickTaskRef(evt, id, result.id);
    return true;
  }
  if (evt.key === 'Escape') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    _terminalComposeTaskDropdownHide(id);
    return true;
  }
  return false;
}

function terminalComposePickTaskRef(evt, cellId, taskId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  var id = String(cellId || '');
  var input = document.getElementById
    ? document.getElementById(_terminalComposeInputId(id))
    : null;
  if (!input) return false;
  var value = String(input.value || '');
  var trigger = _terminalComposeTaskTrigger(input);
  var start = trigger ? trigger.start : (
    typeof input.selectionStart === 'number' ? input.selectionStart : value.length
  );
  var end = trigger ? trigger.end : (
    typeof input.selectionEnd === 'number' ? input.selectionEnd : start
  );
  start = Math.max(0, Math.min(value.length, start));
  end = Math.max(start, Math.min(value.length, end));
  var insertText = String(taskId || '').trim();
  if (!insertText) {
    _terminalComposeTaskDropdownHide(id);
    return false;
  }
  insertText += ' ';
  input.value = value.slice(0, start) + insertText + value.slice(end);
  var cursor = start + insertText.length;
  if (typeof input.setSelectionRange === 'function') {
    input.setSelectionRange(cursor, cursor);
  } else {
    input.selectionStart = cursor;
    input.selectionEnd = cursor;
    if ('selectionDirection' in input) input.selectionDirection = 'none';
  }
  _terminalComposeTaskDropdownHide(id);
  terminalComposeInput(input);
  if (typeof input.focus === 'function') input.focus();
  return false;
}

function _terminalComposeRecallState(cellId) {
  const id = String(cellId || '');
  if (!_terminalComposeRecall[id]) {
    _terminalComposeRecall[id] = { index: -1, draft: '' };
  }
  return _terminalComposeRecall[id];
}

function _terminalComposeResetRecall(cellId) {
  const id = String(cellId || '');
  if (id) delete _terminalComposeRecall[id];
}

function _terminalComposeSetValue(input, cellId, value, options) {
  if (!input) return;
  const id = String(cellId || (input.dataset ? input.dataset.cellId : '') || '');
  input.value = String(value || '');
  const preserveAttachments = !!(options && options.preserveAttachments);
  if (id && !preserveAttachments) _terminalComposePruneAttachments(id, input.value);
  if (id) _terminalComposeTaskDropdownHide(id);
  if (id) _terminalComposeDrafts[id] = input.value;
  const end = input.value.length;
  if (typeof input.setSelectionRange === 'function') {
    input.setSelectionRange(end, end);
  } else {
    input.selectionStart = end;
    input.selectionEnd = end;
    if ('selectionDirection' in input) input.selectionDirection = 'none';
  }
  _terminalComposeAutoResize(input);
  _terminalComposeSetButtonState(input);
}

function _terminalComposeCaretAtFirstLine(input) {
  if (!input || typeof input.value !== 'string') return true;
  const caret = _terminalComposeActiveSelection(input);
  return input.value.lastIndexOf('\n', Math.max(0, caret - 1)) < 0;
}

function _terminalComposeCaretAtLastLine(input) {
  if (!input || typeof input.value !== 'string') return true;
  const caret = _terminalComposeActiveSelection(input);
  return input.value.indexOf('\n', caret) < 0;
}

function _terminalComposeHistoryNavigate(input, cellId, direction) {
  const id = String(cellId || '');
  const entries = _terminalMessageHistoryEntries(id);
  if (!input || !id || !entries.length) return false;
  const recall = _terminalComposeRecallState(id);
  if (recall.index < 0) {
    if (direction > 0) return false;
    recall.draft = String(input.value || '');
    recall.index = 0;
  } else if (direction < 0) {
    recall.index = Math.min(entries.length - 1, recall.index + 1);
  } else {
    recall.index -= 1;
  }

  if (recall.index < 0) {
    const draft = recall.draft || '';
    _terminalComposeResetRecall(id);
    _terminalComposeSetValue(input, id, draft, { preserveAttachments: true });
    return true;
  }

  _terminalComposeSetValue(input, id, entries[recall.index].message, { preserveAttachments: true });
  return true;
}

function _terminalComposeRestoreRecallDraft(input, cellId) {
  const id = String(cellId || '');
  const recall = _terminalComposeRecall[id];
  if (!recall || recall.index < 0) return false;
  const draft = recall.draft || '';
  _terminalComposeResetRecall(id);
  _terminalComposeSetValue(input, id, draft, { preserveAttachments: true });
  return true;
}

function _terminalComposeErrorElement(input) {
  const container = _terminalComposeContainerFor(input);
  return container && container.querySelector
    ? container.querySelector('.terminal-compose-error')
    : null;
}

function _terminalComposeSetError(input, message) {
  if (!input) return;
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  const text = String(message || '');
  if (cellId) _terminalComposeErrors[cellId] = text;
  const el = _terminalComposeErrorElement(input);
  if (el) el.textContent = text;
}

function _terminalComposeSetDropTarget(input, active) {
  if (!input || !input.classList) return;
  input.classList.toggle('terminal-compose-drop-target', !!active);
  const container = _terminalComposeContainerFor(input);
  if (container && container.classList) {
    container.classList.toggle('terminal-compose-drop-target', !!active);
  }
}

function _terminalComposeDroppedFiles(dataTransfer) {
  if (!dataTransfer || !dataTransfer.files || !dataTransfer.files.length) return [];
  return Array.prototype.slice.call(dataTransfer.files);
}

function _terminalComposeHasDraggedFiles(dataTransfer) {
  if (!dataTransfer) return false;
  if (dataTransfer.types && typeof dataTransfer.types.length === 'number') {
    if (Array.prototype.indexOf.call(dataTransfer.types, 'Files') !== -1) return true;
  }
  if (dataTransfer.items && typeof dataTransfer.items.length === 'number') {
    for (var i = 0; i < dataTransfer.items.length; i++) {
      var item = dataTransfer.items[i];
      if (item && item.kind === 'file') return true;
    }
  }
  return _terminalComposeDroppedFiles(dataTransfer).length > 0;
}

function _terminalComposeNormalizeMime(type) {
  return String(type || '').split(';')[0].trim().toLowerCase();
}

function _terminalComposeFormatBytes(bytes) {
  var mb = Math.floor(bytes / (1024 * 1024));
  return (mb || 1) + ' MB';
}

function _terminalComposeFileLabel(file) {
  return file && file.name ? String(file.name) : 'Dropped file';
}

function terminalComposeValidateDroppedFiles(files) {
  var accepted = [];
  var errors = [];
  var list = Array.prototype.slice.call(files || []);
  for (var i = 0; i < list.length; i++) {
    var file = list[i];
    var mime = _terminalComposeNormalizeMime(file && file.type);
    var name = _terminalComposeFileLabel(file);
    if (!TERMINAL_COMPOSE_ATTACHMENT_MIME_TYPES[mime]) {
      errors.push(name + ' is not a supported image type.');
      continue;
    }
    if (file && typeof file.size === 'number'
        && file.size > TERMINAL_COMPOSE_ATTACHMENT_MAX_BYTES) {
      errors.push(name + ' is larger than '
        + _terminalComposeFormatBytes(TERMINAL_COMPOSE_ATTACHMENT_MAX_BYTES) + '.');
      continue;
    }
    accepted.push(file);
  }
  return { accepted: accepted, errors: errors };
}

function _terminalComposeAttachmentToken(number) {
  return '[ Image #' + number + ' ]';
}

function _terminalComposeAttachmentState(cellId) {
  var id = String(cellId || '');
  if (!id) return null;
  if (!_terminalComposeAttachments[id]) {
    _terminalComposeAttachments[id] = { next: 1, entries: [] };
  }
  var stateForCell = _terminalComposeAttachments[id];
  if (!Array.isArray(stateForCell.entries)) stateForCell.entries = [];
  var next = Number(stateForCell.next || 1);
  stateForCell.next = Number.isFinite(next) && next > 0 ? Math.floor(next) : 1;
  return stateForCell;
}

function _terminalComposeHighestAttachmentTokenNumber(text) {
  var highest = 0;
  var re = /\[ Image #(\d+) \]/g;
  var match = null;
  text = String(text || '');
  while ((match = re.exec(text))) {
    highest = Math.max(highest, Number(match[1]) || 0);
  }
  return highest;
}

function _terminalComposeRegisterAttachmentPaths(cellId, paths, displayText) {
  var stateForCell = _terminalComposeAttachmentState(cellId);
  if (!stateForCell) {
    return (paths || []).map(function(path) { return String(path || ''); })
      .filter(Boolean);
  }
  stateForCell.next = Math.max(
    stateForCell.next || 1,
    _terminalComposeHighestAttachmentTokenNumber(displayText) + 1
  );
  var tokens = [];
  for (var i = 0; i < (paths || []).length; i++) {
    var path = String(paths[i] || '');
    if (!path) continue;
    var token = _terminalComposeAttachmentToken(stateForCell.next);
    stateForCell.next += 1;
    stateForCell.entries.push({ token: token, path: path });
    tokens.push(token);
  }
  return tokens;
}

function _terminalComposePruneAttachments(cellId, displayText) {
  var id = String(cellId || '');
  var stateForCell = id ? _terminalComposeAttachments[id] : null;
  if (!stateForCell || !Array.isArray(stateForCell.entries)) return;
  var text = String(displayText || '');
  stateForCell.entries = stateForCell.entries.filter(function(entry) {
    return !!(entry && entry.token && text.indexOf(entry.token) !== -1);
  });
  if (!stateForCell.entries.length) delete _terminalComposeAttachments[id];
}

function _terminalComposePayloadText(cellId, displayText) {
  var id = String(cellId || '');
  var text = String(displayText || '');
  if (!id || !_terminalComposeAttachments[id]) return text;
  _terminalComposePruneAttachments(id, text);
  var stateForCell = _terminalComposeAttachments[id];
  var entries = stateForCell && Array.isArray(stateForCell.entries)
    ? stateForCell.entries
    : [];
  for (var i = 0; i < entries.length; i++) {
    var entry = entries[i];
    if (!entry || !entry.token) continue;
    text = text.split(entry.token).join(String(entry.path || ''));
  }
  return text;
}

function _terminalComposeInsertPaths(input, paths) {
  if (!input || !paths || !paths.length) return;
  var cellId = input.dataset ? (input.dataset.cellId || '') : '';
  var insertText = _terminalComposeRegisterAttachmentPaths(
    cellId,
    paths,
    input.value
  ).join('\n');
  if (!insertText) return;
  var value = String(input.value || '');
  var start = typeof input.selectionStart === 'number' ? input.selectionStart : value.length;
  var end = typeof input.selectionEnd === 'number' ? input.selectionEnd : start;
  start = Math.max(0, Math.min(value.length, start));
  end = Math.max(start, Math.min(value.length, end));
  input.value = value.slice(0, start) + insertText + value.slice(end);
  var cursor = start + insertText.length;
  if (typeof input.setSelectionRange === 'function') {
    input.setSelectionRange(cursor, cursor);
  } else {
    input.selectionStart = cursor;
    input.selectionEnd = cursor;
  }
  terminalComposeInput(input);
  if (typeof input.focus === 'function') input.focus();
}

async function _terminalComposeUploadAttachments(cellId, files) {
  var fd = new FormData();
  fd.append('agent_id', String(cellId || ''));
  for (var i = 0; i < files.length; i++) {
    fd.append('file', files[i]);
  }
  var r = await fetch('/api/attachment/upload', { method: 'POST', body: fd });
  var res = r && typeof r.json === 'function' ? await r.json() : null;
  if (!res || !res.ok) {
    throw new Error((res && res.error) || 'Attachment upload failed.');
  }
  if (!Array.isArray(res.data)) {
    throw new Error('Attachment upload failed.');
  }
  return res.data
    .map(function(entry) { return entry && entry.path ? entry.path : ''; })
    .filter(Boolean);
}

function _terminalComposePersistFromDom(root) {
  const input = _terminalComposeTextarea(root);
  if (!input || !input.dataset || !input.dataset.cellId) return;
  _terminalComposeDrafts[input.dataset.cellId] = String(input.value || '');
}

function _captureTerminalWorkspaceState(root, cell) {
  if (typeof _captureSurfaceState !== 'function') return null;
  const snapshot = _captureSurfaceState(root);
  const active = document.activeElement;
  if (snapshot && snapshot.focus && active && active.dataset
      && active.dataset.cellId
      && cell && active.dataset.cellId !== String(cell.id || '')) {
    snapshot.focus = null;
  }
  if (snapshot) snapshot.terminalDirectMessages = _captureTerminalDirectMessagesState(root);
  return snapshot;
}

function _terminalWorkspaceFocusedComposeHasDraft(root) {
  if (!root || typeof document === 'undefined') return false;
  const active = document.activeElement;
  if (!active) return false;
  if (typeof root.contains === 'function' && !root.contains(active)) return false;
  if (!(active.classList && active.classList.contains('terminal-compose-input'))) return false;
  return String(active.value || '').length > 0;
}

function _restoreTerminalWorkspaceState(root, snapshot, cell) {
  if (typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(root, snapshot);
  }
  _restoreTerminalDirectMessagesState(root, snapshot);
  const input = _terminalComposeTextarea(root);
  if (!input) return;
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  // Only re-assign value if the rendered textarea actually drifted from the
  // in-memory draft. Re-assigning a textarea's value resets its scrollTop
  // and would undo the cursor-into-view scroll that _restoreSurfaceState
  // just performed.
  if (cell && cellId === String(cell.id || '')
      && Object.prototype.hasOwnProperty.call(_terminalComposeDrafts, cellId)
      && input.value !== _terminalComposeDrafts[cellId]) {
    input.value = _terminalComposeDrafts[cellId];
  }
  _terminalComposeAutoResize(input);
  _terminalComposeSetButtonState(input);
}

function _renderTerminalCompose(root, cell) {
  if (!root) return;
  const directAgent = _terminalDirectMessageAgent(cell);
  if (!cell || (!cell.session_id && !directAgent)) {
    if (root.innerHTML !== '') root.innerHTML = '';
    return;
  }
  const cellId = String(cell.id || '');
  const directAgentId = directAgent ? String(directAgent.id || '') : '';
  const inputId = _terminalComposeInputId(cellId);
  const buttonId = _terminalComposeButtonId(cellId);
  const historyButtonId = _terminalComposeHistoryButtonId(cellId);
  const historyMenuId = _terminalComposeHistoryMenuId(cellId);
  const taskDropdownId = _terminalComposeTaskDropdownId(cellId);
  const draft = Object.prototype.hasOwnProperty.call(_terminalComposeDrafts, cellId)
    ? _terminalComposeDrafts[cellId]
    : '';
  const error = Object.prototype.hasOwnProperty.call(_terminalComposeErrors, cellId)
    ? _terminalComposeErrors[cellId]
    : '';
  const disabled = !String(draft || '').trim();
  const placeholder = 'Send a message to ' + ((directAgent && directAgent.name) || cell.name || 'terminal') + '\u2026';
  const replyToId = directAgentId
    ? String(_terminalDirectMessageReplyToByAgent[directAgentId] || '')
    : '';
  const replyRow = replyToId ? _terminalDirectMessageById(directAgentId, replyToId) : null;
  const replyHtml = replyToId
    ? '<div class="terminal-compose-reply" data-reply-to-id="' + esc(replyToId) + '">'
      + '<span>Replying to ' + esc(replyRow ? _terminalDirectMessagePreview(replyRow) : replyToId) + '</span>'
      + '<button type="button" class="terminal-compose-reply-cancel"'
      + ' onclick="return terminalDirectMessageCancelReply(event, \'' + esc(directAgentId) + '\')"'
      + ' aria-label="Cancel reply">×</button>'
      + '</div>'
    : '';

  // Idempotent path: if the form already exists for this cell, update only
  // the dynamic bits (placeholder, error, button disabled, draft value if it
  // drifted) without clobbering the textarea \u2014 clobbering destroys focus and
  // produces the TORQUE:264 textbox-border flicker under multi-agent activity.
  const existingForm = root.querySelector ? root.querySelector('.terminal-compose') : null;
  const existingCellId = existingForm && existingForm.dataset
    ? String(existingForm.dataset.cellId || '')
    : '';
  const existingReplyToId = existingForm && existingForm.dataset
    ? String(existingForm.dataset.replyToId || '')
    : '';
  const existingDirectAgentId = existingForm && existingForm.dataset
    ? String(existingForm.dataset.agentId || '')
    : '';
  if (existingForm
      && existingCellId === cellId
      && existingReplyToId === replyToId
      && existingDirectAgentId === directAgentId) {
    const input = _terminalComposeTextarea(root);
    if (input) {
      if (input.placeholder !== placeholder) input.placeholder = placeholder;
      if (input.value !== draft) input.value = draft;
      _terminalComposeAutoResize(input);
      _terminalComposeSetButtonState(input);
    }
    const errorEl = existingForm.querySelector
      ? existingForm.querySelector('.terminal-compose-error')
      : null;
    if (errorEl && errorEl.textContent !== error) errorEl.textContent = error;
    const button = _terminalComposeButtonFor(input, cellId);
    if (button) {
      const shouldDisable = !!disabled;
      if (button.disabled !== shouldDisable) button.disabled = shouldDisable;
    }
    return;
  }

  root.innerHTML = ''
    + '<form class="terminal-compose" data-cell-id="' + esc(cellId) + '"'
    + (directAgentId ? ' data-agent-id="' + esc(directAgentId) + '"' : '')
    + (replyToId ? ' data-reply-to-id="' + esc(replyToId) + '"' : '')
    + ' onsubmit="return terminalComposeSubmit(event, \'' + esc(cellId) + '\')">'
    + '  <div class="terminal-compose-input-wrap">'
    + replyHtml
    + '  <textarea id="' + esc(inputId) + '" class="terminal-compose-input" rows="1"'
    + ' data-cell-id="' + esc(cellId) + '"'
    + (directAgentId ? ' data-agent-id="' + esc(directAgentId) + '"' : '')
    + ' placeholder="' + esc(placeholder) + '"'
    + ' oninput="terminalComposeInput(this)"'
    + ' onkeydown="terminalComposeKeydown(event, \'' + esc(cellId) + '\')"'
    + ' ondragenter="terminalComposeDragenter(event, \'' + esc(cellId) + '\')"'
    + ' ondragover="terminalComposeDragover(event, \'' + esc(cellId) + '\')"'
    + ' ondragleave="terminalComposeDragleave(event, \'' + esc(cellId) + '\')"'
    + ' ondrop="terminalComposeDrop(event, \'' + esc(cellId) + '\')">' + esc(draft) + '</textarea>'
    + '  <div class="terminal-compose-error" aria-live="polite">' + esc(error) + '</div>'
    + '  <div id="' + esc(taskDropdownId) + '"'
    + ' class="deps-dropdown terminal-compose-task-dropdown"'
    + ' role="listbox" aria-label="Matching tickets" style="display:none"></div>'
    + '  </div>'
    + '  <button id="' + esc(buttonId) + '" class="terminal-compose-submit" type="submit"'
    + (disabled ? ' disabled' : '')
    + ' title="Send message">Send</button>'
    + '  <div class="terminal-compose-history-wrap">'
    + '    <button id="' + esc(historyButtonId) + '" class="terminal-compose-history-toggle" type="button"'
    + ' onclick="return terminalComposeHistoryToggle(event, \'' + esc(cellId) + '\')"'
    + ' title="Message history (use \u2191/\u2193 to recall)" aria-label="Message history"'
    + ' aria-haspopup="listbox" aria-expanded="false" aria-controls="' + esc(historyMenuId) + '">'
    + '<span class="terminal-compose-history-icon" aria-hidden="true">\u21ba</span></button>'
    + '    <div id="' + esc(historyMenuId) + '" class="terminal-compose-history-menu"'
    + ' role="listbox" aria-label="Recent messages" hidden></div>'
    + '  </div>'
    + '</form>';
  const input = _terminalComposeTextarea(root);
  if (input) {
    input.value = draft;
    _terminalComposeAutoResize(input);
    _terminalComposeSetButtonState(input);
  }
}

function terminalComposeInput(el) {
  if (!el) return;
  const cellId = el.dataset ? (el.dataset.cellId || '') : '';
  if (cellId) _terminalComposeResetRecall(cellId);
  if (cellId) _terminalComposePruneAttachments(cellId, el.value);
  if (cellId) _terminalComposeDrafts[cellId] = String(el.value || '');
  if (cellId && _terminalComposeErrors[cellId]) _terminalComposeSetError(el, '');
  _terminalComposeAutoResize(el);
  _terminalComposeSetButtonState(el);
  _terminalComposeUpdateTaskDropdown(el);
}

function terminalComposeClear(cellId) {
  const id = String(cellId || '');
  const input = document.getElementById ? document.getElementById(_terminalComposeInputId(id)) : null;
  if (!input) return;
  _terminalComposeResetRecall(id);
  _terminalComposeHistoryClose(id);
  _terminalComposeTaskDropdownHide(id);
  input.value = '';
  if (id) _terminalComposeDrafts[id] = '';
  if (id) delete _terminalComposeAttachments[id];
  _terminalComposeAutoResize(input);
  _terminalComposeSetButtonState(input);
}

function _terminalComposeActiveSelection(input) {
  if (!input) return 0;
  if (input.selectionDirection === 'backward' && typeof input.selectionStart === 'number') {
    return input.selectionStart;
  }
  return typeof input.selectionEnd === 'number' ? input.selectionEnd : 0;
}

function _terminalComposeSelectionAnchor(input) {
  if (!input) return 0;
  if (input.selectionDirection === 'backward' && typeof input.selectionEnd === 'number') {
    return input.selectionEnd;
  }
  return typeof input.selectionStart === 'number' ? input.selectionStart : 0;
}

function _terminalComposeLineBoundary(value, caret, toEnd) {
  if (toEnd) {
    var lineEnd = value.indexOf('\n', caret);
    return lineEnd >= 0 ? lineEnd : value.length;
  }
  var lineStart = value.lastIndexOf('\n', caret > 0 ? caret - 1 : -1);
  return lineStart >= 0 ? lineStart + 1 : 0;
}

function _terminalComposeSetSelection(input, start, end, direction) {
  if (!input) return;
  var valueLength = typeof input.value === 'string' ? input.value.length : 0;
  start = Math.max(0, Math.min(valueLength, start));
  end = Math.max(0, Math.min(valueLength, end));
  if (typeof input.setSelectionRange === 'function') {
    input.setSelectionRange(start, end, direction || 'none');
  } else {
    input.selectionStart = start;
    input.selectionEnd = end;
    if ('selectionDirection' in input) input.selectionDirection = direction || 'none';
  }
}

function _terminalComposeMoveCaret(input, evt, toEnd, wholeBuffer) {
  if (!input || typeof input.value !== 'string') return false;
  var active = _terminalComposeActiveSelection(input);
  var anchor = _terminalComposeSelectionAnchor(input);
  var target = wholeBuffer
    ? (toEnd ? input.value.length : 0)
    : _terminalComposeLineBoundary(input.value, active, toEnd);
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt.shiftKey) {
    _terminalComposeSetSelection(
      input,
      Math.min(anchor, target),
      Math.max(anchor, target),
      target < anchor ? 'backward' : 'forward'
    );
  } else {
    _terminalComposeSetSelection(input, target, target, 'none');
  }
  return true;
}

function _terminalComposeScrollToBottom(cellId) {
  const id = String(cellId || '');
  for (const key in _embeddedTerminalSessions) {
    const entry = _embeddedTerminalSessions[key];
    if (entry && entry.cellId === id) _embeddedTerminalScrollToTail(entry);
  }
}

function _terminalComposeDirectAgentForCellId(cellId) {
  const id = String(cellId || '').trim();
  const cell = id && state && state.agents ? state.agents[id] : null;
  return _terminalDirectMessageAgent(cell);
}

function _terminalComposeNextIdempotencyKey(agentId) {
  _terminalDirectMessageIdempotencyCounter += 1;
  return [
    'terminal-direct',
    String(agentId || 'agent'),
    Date.now(),
    _terminalDirectMessageIdempotencyCounter,
  ].join(':');
}

function terminalComposeSubmit(evt, cellId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  const id = String(cellId || '');
  let input = null;
  if (evt && evt.currentTarget && evt.currentTarget.querySelector) {
    input = evt.currentTarget.querySelector('.terminal-compose-input');
  }
  if (!input && document.getElementById) {
    input = document.getElementById(_terminalComposeInputId(id));
  }
  if (!input) return false;
  const displayText = String(input.value || '');
  if (!displayText.trim()) {
    terminalComposeClear(id);
    return false;
  }
  const text = _terminalComposePayloadText(id, displayText);
  const directAgent = _terminalComposeDirectAgentForCellId(id);
  if (directAgent && directAgent.id) {
    const directAgentId = String(directAgent.id || '');
    const replyToId = String(_terminalDirectMessageReplyToByAgent[directAgentId] || '');
    const payload = {
      cmd: 'user_agent_message',
      agent_id: directAgentId,
      message: text,
      thread_id: 'user-agent:user:' + directAgentId,
      idempotency_key: _terminalComposeNextIdempotencyKey(directAgentId),
    };
    if (replyToId) payload.reply_to_id = replyToId;
    send(payload);
    if (replyToId) delete _terminalDirectMessageReplyToByAgent[directAgentId];
  } else {
    send({ cmd: 'send_user_message', cell_id: id, text: text });
  }
  _terminalComposeScrollToBottom(id);
  terminalComposeClear(id);
  if (directAgent && typeof renderTerminalWorkspace === 'function') renderTerminalWorkspace();
  return false;
}

function terminalComposeKeydown(evt, cellId) {
  if (!evt) return;
  if (_terminalComposeTaskDropdownHandleKey(evt, cellId)) return;
  if (evt.key === 'Escape' && _terminalComposeHistoryIsOpen(cellId)) {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    _terminalComposeHistoryClose(cellId);
    return;
  }
  if ((evt.key === 'ArrowUp' || evt.key === 'ArrowDown')
      && !evt.shiftKey && !evt.ctrlKey && !evt.metaKey && !evt.altKey) {
    const input = evt.target && typeof evt.target.value === 'string'
      ? evt.target
      : (document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null);
    const id = String(cellId || (input && input.dataset ? input.dataset.cellId : '') || '');
    const recall = id ? _terminalComposeRecall[id] : null;
    const recallActive = !!(recall && recall.index >= 0);
    const direction = evt.key === 'ArrowUp' ? -1 : 1;
    const shouldRecall = (
      direction < 0
        ? _terminalComposeCaretAtFirstLine(input)
        : recallActive && _terminalComposeCaretAtLastLine(input)
    );
    if (shouldRecall && _terminalComposeHistoryNavigate(input, id, direction)) {
      if (typeof evt.preventDefault === 'function') evt.preventDefault();
      if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
      return;
    }
  }
  if ((evt.key === 'Home' || evt.key === 'End') && !evt.altKey) {
    const input = evt.target && typeof evt.target.value === 'string'
      ? evt.target
      : (document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null);
    if (_terminalComposeMoveCaret(input, evt, evt.key === 'End', !!(evt.metaKey || evt.ctrlKey))) {
      _terminalComposeUpdateTaskDropdown(input);
      return;
    }
  }
  if (evt.key === 'Escape') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    const input = evt.target && typeof evt.target.value === 'string'
      ? evt.target
      : (document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null);
    if (_terminalComposeRestoreRecallDraft(input, cellId)) {
      return;
    }
    terminalComposeClear(cellId);
    return;
  }
  if (evt.key === 'Enter' && !evt.shiftKey) {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    terminalComposeSubmit(evt, cellId);
  }
}

function terminalComposeDragenter(evt, cellId) {
  if (!evt || !_terminalComposeHasDraggedFiles(evt.dataTransfer)) return;
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  const input = evt.currentTarget || (
    document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null
  );
  _terminalComposeSetDropTarget(input, true);
}

function terminalComposeDragover(evt, cellId) {
  if (!evt || !_terminalComposeHasDraggedFiles(evt.dataTransfer)) return;
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt.dataTransfer) evt.dataTransfer.dropEffect = 'copy';
  const input = evt.currentTarget || (
    document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null
  );
  _terminalComposeSetDropTarget(input, true);
}

function terminalComposeDragleave(evt, cellId) {
  if (!evt || !_terminalComposeHasDraggedFiles(evt.dataTransfer)) return;
  const input = evt.currentTarget || (
    document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null
  );
  _terminalComposeSetDropTarget(input, false);
}

async function terminalComposeDrop(evt, cellId) {
  if (!evt || !_terminalComposeHasDraggedFiles(evt.dataTransfer)) return false;
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
  const id = String(cellId || '');
  const input = evt.currentTarget || (
    document.getElementById ? document.getElementById(_terminalComposeInputId(id)) : null
  );
  _terminalComposeSetDropTarget(input, false);

  var files = _terminalComposeDroppedFiles(evt.dataTransfer);
  if (!files.length) {
    _terminalComposeSetError(input, 'Drop an image file to attach it.');
    return false;
  }
  var validation = terminalComposeValidateDroppedFiles(files);
  if (validation.errors.length) {
    _terminalComposeSetError(input, validation.errors[0]);
    return false;
  }
  if (!validation.accepted.length) {
    _terminalComposeSetError(input, 'Drop an image file to attach it.');
    return false;
  }

  try {
    _terminalComposeSetError(input, '');
    var paths = await _terminalComposeUploadAttachments(id, validation.accepted);
    if (!paths.length) {
      _terminalComposeSetError(input, 'Attachment upload failed.');
      return false;
    }
    _terminalComposeInsertPaths(input, paths);
  } catch (e) {
    _terminalComposeSetError(input, (e && e.message) || 'Attachment upload failed.');
  }
  return false;
}

function _setActiveEmbeddedTerminalEntry(entry) {
  _embeddedTerminal = entry ? entry.terminal : null;
  _embeddedTerminalFit = entry ? entry.fit : null;
  _embeddedTerminalWs = entry ? entry.ws : null;
  _embeddedTerminalSessionKey = entry ? entry.sessionKey : '';
  _embeddedTerminalResizeObserver = entry ? entry.resizeObserver : null;
  _embeddedTerminalDataHandler = entry ? entry.dataHandler : null;
  _embeddedTerminalDropSurface = entry ? entry.dropSurface : null;
  _embeddedTerminalDropHandlers = entry ? entry.dropHandlers : null;
  _embeddedTerminalDropDepth = entry ? (entry.dropDepth || 0) : 0;
}

function _isEmbeddedTerminalEntryActive(entry) {
  return !!(entry
    && _embeddedTerminalSessionKey === entry.sessionKey
    && _embeddedTerminalSessions[entry.sessionKey] === entry);
}

function _disposeEmbeddedTerminalEntry(entry) {
  if (!entry) return;
  if (_embeddedTerminalSessions[entry.sessionKey] === entry) {
    delete _embeddedTerminalSessions[entry.sessionKey];
  }
  _detachEmbeddedTerminalTailControls(entry);
  if (entry.dropSurface && entry.dropHandlers
      && typeof entry.dropSurface.removeEventListener === 'function') {
    entry.dropSurface.removeEventListener('dragenter', entry.dropHandlers.dragenter, true);
    entry.dropSurface.removeEventListener('dragover', entry.dropHandlers.dragover, true);
    entry.dropSurface.removeEventListener('dragleave', entry.dropHandlers.dragleave, true);
    entry.dropSurface.removeEventListener('drop', entry.dropHandlers.drop, true);
    _setEmbeddedTerminalDropTarget(entry.dropSurface, false);
  }
  if (entry.resizeObserver) entry.resizeObserver.disconnect();
  if (entry.ws) {
    entry.ws.onopen = null;
    entry.ws.onmessage = null;
    entry.ws.onerror = null;
    entry.ws.onclose = null;
    entry.ws.close();
  }
  if (entry.dataHandler && typeof entry.dataHandler.dispose === 'function') {
    entry.dataHandler.dispose();
  }
  if (entry.terminal) entry.terminal.dispose();
  if (entry.surface && typeof entry.surface.remove === 'function') entry.surface.remove();
  if (_embeddedTerminalSessionKey === entry.sessionKey) _setActiveEmbeddedTerminalEntry(null);
}

function _closeEmbeddedTerminalEntrySocket(entry) {
  if (!entry || !entry.ws) return;
  const socket = entry.ws;
  socket.onopen = null;
  socket.onmessage = null;
  socket.onerror = null;
  socket.onclose = null;
  socket.close();
  if (entry.ws === socket) entry.ws = null;
  if (_embeddedTerminalWs === socket) _embeddedTerminalWs = null;
}

function _findEmbeddedTerminalEntryForCell(cellId) {
  let fallback = null;
  for (const key of Object.keys(_embeddedTerminalSessions)) {
    const entry = _embeddedTerminalSessions[key];
    if (!entry || entry.cellId !== cellId) continue;
    if (state.agents[cellId] && _terminalCellIsTombstoned(state.agents[cellId])) continue;
    if (_embeddedTerminalSessionKey === entry.sessionKey) return entry;
    if (!fallback) fallback = entry;
  }
  return fallback;
}

function _disposeEmbeddedTerminalEntriesForCell(cellId, keepSessionKey) {
  for (const key of Object.keys(_embeddedTerminalSessions)) {
    const entry = _embeddedTerminalSessions[key];
    if (entry && entry.cellId === cellId && key !== keepSessionKey) {
      _disposeEmbeddedTerminalEntry(entry);
    }
  }
}

function _disposeEmbeddedTerminal() {
  for (const key of Object.keys(_embeddedTerminalSessions)) {
    _disposeEmbeddedTerminalEntry(_embeddedTerminalSessions[key]);
  }
  if (_embeddedTerminalDropSurface && _embeddedTerminalDropHandlers
      && typeof _embeddedTerminalDropSurface.removeEventListener === 'function') {
    _embeddedTerminalDropSurface.removeEventListener('dragenter', _embeddedTerminalDropHandlers.dragenter, true);
    _embeddedTerminalDropSurface.removeEventListener('dragover', _embeddedTerminalDropHandlers.dragover, true);
    _embeddedTerminalDropSurface.removeEventListener('dragleave', _embeddedTerminalDropHandlers.dragleave, true);
    _embeddedTerminalDropSurface.removeEventListener('drop', _embeddedTerminalDropHandlers.drop, true);
    _setEmbeddedTerminalDropTarget(_embeddedTerminalDropSurface, false);
  }
  if (_embeddedTerminalResizeObserver) _embeddedTerminalResizeObserver.disconnect();
  if (_embeddedTerminalWs) {
    _embeddedTerminalWs.onopen = null;
    _embeddedTerminalWs.onmessage = null;
    _embeddedTerminalWs.onerror = null;
    _embeddedTerminalWs.onclose = null;
    _embeddedTerminalWs.close();
  }
  if (_embeddedTerminalDataHandler && typeof _embeddedTerminalDataHandler.dispose === 'function') {
    _embeddedTerminalDataHandler.dispose();
  }
  if (_embeddedTerminal) _embeddedTerminal.dispose();
  _setActiveEmbeddedTerminalEntry(null);
  _embeddedTerminalSessionKey = '';
  _embeddedTerminalPendingFocusKey = '';
}

function _deactivateEmbeddedTerminalWorkspace() {
  _setActiveEmbeddedTerminalEntry(null);
  _embeddedTerminalPendingFocusKey = '';
}

function _embeddedTerminalUrl(cell) {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var url = protocol + '//' + location.host + '/ws/terminal/' + encodeURIComponent(cell.id);
  var clientId = '';
  try {
    if (typeof _torqueClientId === 'function') clientId = _torqueClientId();
  } catch (_err) {}
  if (!clientId) {
    try {
      if (typeof TORQUE_CLIENT_ID !== 'undefined') clientId = TORQUE_CLIENT_ID;
    } catch (_err2) {}
  }
  if (!clientId) return url;
  return url + '?client_id=' + encodeURIComponent(clientId);
}

function _embeddedTerminalDroppedFiles(dataTransfer) {
  if (!dataTransfer || !dataTransfer.files || !dataTransfer.files.length) return [];
  return Array.prototype.slice.call(dataTransfer.files);
}

function _embeddedTerminalHasDraggedFiles(dataTransfer) {
  if (!dataTransfer) return false;
  if (dataTransfer.types && typeof dataTransfer.types.length === 'number') {
    if (Array.prototype.indexOf.call(dataTransfer.types, 'Files') !== -1) return true;
  }
  if (dataTransfer.items && typeof dataTransfer.items.length === 'number') {
    for (var i = 0; i < dataTransfer.items.length; i++) {
      var item = dataTransfer.items[i];
      if (item && item.kind === 'file') return true;
    }
  }
  return _embeddedTerminalDroppedFiles(dataTransfer).length > 0;
}

function _embeddedTerminalDroppedImages(dataTransfer) {
  var files = _embeddedTerminalDroppedFiles(dataTransfer);
  return files.filter(function(file) {
    return !!(file && file.type && file.type.indexOf('image/') === 0);
  });
}

function _embeddedTerminalDraggedImagesVisible(dataTransfer) {
  if (dataTransfer && dataTransfer.items && typeof dataTransfer.items.length === 'number') {
    var sawFile = false;
    for (var i = 0; i < dataTransfer.items.length; i++) {
      var item = dataTransfer.items[i];
      if (!item || item.kind !== 'file') continue;
      sawFile = true;
      if (item.type && item.type.indexOf('image/') === 0) return true;
    }
    if (sawFile) return false;
  }
  return _embeddedTerminalDroppedImages(dataTransfer).length > 0
    || _embeddedTerminalHasDraggedFiles(dataTransfer);
}

function _embeddedTerminalDropUploadId(cell) {
  var raw = 'terminal-drop-' + (cell && cell.id ? cell.id : 'session') + '-' + (cell && cell.session_id ? cell.session_id : 'session');
  raw = raw.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
  return raw || 'terminal-drop';
}

function _shellQuoteTerminalPath(path) {
  return "'" + String(path || '').replace(/'/g, "'\"'\"'") + "'";
}

function _setEmbeddedTerminalDropTarget(surface, active) {
  if (!surface || !surface.classList) return;
  surface.classList.toggle('terminal-drop-target', !!active);
  var status = document.querySelector('#terminal-workspace .terminal-statusbar');
  if (status && status.classList) status.classList.toggle('terminal-drop-target', !!active);
}

async function _uploadEmbeddedTerminalImages(cell, files) {
  var taskId = _embeddedTerminalDropUploadId(cell);
  var uploaded = await Promise.all(files.map(async function(file) {
    var fd = new FormData();
    fd.append('task_id', taskId);
    fd.append('file', file);
    try {
      var r = await fetch('/api/upload', { method: 'POST', body: fd });
      var res = await r.json();
      if (!res || !res.ok || !Array.isArray(res.data)) return [];
      return res.data
        .map(function(entry) { return entry && entry.path ? entry.path : ''; })
        .filter(Boolean);
    } catch (e) {
      return [];
    }
  }));
  return uploaded.reduce(function(paths, batch) {
    return paths.concat(batch);
  }, []);
}

function _attachEmbeddedTerminalDropHandlers(cell, surface, entry) {
  if (!surface || typeof surface.addEventListener !== 'function') return;
  entry.cell = cell;
  entry.dropSurface = surface;
  entry.dropDepth = 0;
  entry.dropHandlers = {
    dragenter: function(e) {
      if (!_embeddedTerminalHasDraggedFiles(e && e.dataTransfer)) return;
      entry.dropDepth += 1;
      _setEmbeddedTerminalDropTarget(
        surface, _embeddedTerminalDraggedImagesVisible(e.dataTransfer));
    },
    dragover: function(e) {
      if (!_embeddedTerminalHasDraggedFiles(e && e.dataTransfer)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
      _setEmbeddedTerminalDropTarget(
        surface, _embeddedTerminalDraggedImagesVisible(e.dataTransfer));
    },
    dragleave: function(e) {
      if (!_embeddedTerminalHasDraggedFiles(e && e.dataTransfer)) return;
      entry.dropDepth = Math.max(0, entry.dropDepth - 1);
      if (entry.dropDepth > 0) return;
      if (e.relatedTarget && typeof surface.contains === 'function' && surface.contains(e.relatedTarget)) {
        return;
      }
      _setEmbeddedTerminalDropTarget(surface, false);
    },
    drop: async function(e) {
      var files = _embeddedTerminalDroppedFiles(e && e.dataTransfer);
      if (!files.length) return;
      e.preventDefault();
      entry.dropDepth = 0;
      _setEmbeddedTerminalDropTarget(surface, false);
      var sessionKey = entry.sessionKey;
      var images = _embeddedTerminalDroppedImages(e.dataTransfer);
      if (!images.length) {
        if (_isEmbeddedTerminalEntryActive(entry)) {
          _embeddedTerminalPendingFocusKey = sessionKey;
          focusEmbeddedTerminalWorkspace(false);
        }
        return;
      }
      var paths = await _uploadEmbeddedTerminalImages(entry.cell || cell, images);
      if (!_isEmbeddedTerminalEntryActive(entry)) return;
      if (paths.length && entry.ws && entry.ws.readyState === WebSocket.OPEN) {
        entry.ws.send(JSON.stringify({
          type: 'input',
          data: paths.map(_shellQuoteTerminalPath).join(' ') + ' ',
        }));
      }
      _embeddedTerminalPendingFocusKey = sessionKey;
      focusEmbeddedTerminalWorkspace(false);
    },
  };
  // Capture-phase listeners ensure the workspace still sees file drags
  // before xterm's helper textarea can swallow them.
  surface.addEventListener('dragenter', entry.dropHandlers.dragenter, true);
  surface.addEventListener('dragover', entry.dropHandlers.dragover, true);
  surface.addEventListener('dragleave', entry.dropHandlers.dragleave, true);
  surface.addEventListener('drop', entry.dropHandlers.drop, true);
  if (_isEmbeddedTerminalEntryActive(entry)) _setActiveEmbeddedTerminalEntry(entry);
}

function _updateEmbeddedTerminalEntrySession(entry, cell, sessionKey, sessionId) {
  const oldSessionKey = entry.sessionKey || '';
  if (oldSessionKey && oldSessionKey !== sessionKey
      && _embeddedTerminalSessions[oldSessionKey] === entry) {
    delete _embeddedTerminalSessions[oldSessionKey];
  }
  entry.sessionKey = sessionKey;
  entry.cellId = cell.id;
  entry.cell = cell;
  entry.sessionId = sessionId;
  if (typeof entry.tailPinned !== 'boolean') entry.tailPinned = true;
  _embeddedTerminalSessions[sessionKey] = entry;
  if (entry.surface) {
    if (entry.surface.dataset) entry.surface.dataset.torqueSessionKey = sessionKey;
    if (typeof entry.surface.setAttribute === 'function') {
      entry.surface.setAttribute('data-torque-session-key', sessionKey);
    }
  }
}

function _writeEmbeddedTerminalSessionRestartedSeparator(entry) {
  const term = entry && entry.terminal;
  if (!term) return;
  const line = '──── Torque session restarted ────';
  if (typeof term.writeln === 'function') {
    term.writeln(line);
  } else if (typeof term.write === 'function') {
    term.write('\r\n' + line + '\r\n');
  }
  if (typeof term.write === 'function') {
    const rows = Math.max(1, Math.min(200, Number(term.rows) || 24));
    term.write(new Array(rows + 1).join('\r\n'));
  }
}

function _embeddedTerminalViewport(entry) {
  const surface = entry && entry.surface;
  return surface && typeof surface.querySelector === 'function'
    ? surface.querySelector('.xterm-viewport')
    : null;
}

function _embeddedTerminalViewportAtTail(viewport) {
  if (!viewport) return true;
  const scrollTop = Number(viewport.scrollTop) || 0;
  const clientHeight = Number(viewport.clientHeight) || 0;
  const scrollHeight = Number(viewport.scrollHeight) || 0;
  return scrollHeight <= clientHeight
    || (scrollHeight - scrollTop - clientHeight) <= EMBEDDED_TERMINAL_TAIL_THRESHOLD_PX;
}

function _embeddedTerminalAtTail(entry) {
  const term = entry && entry.terminal;
  const buffer = term && term.buffer && term.buffer.active;
  let bufferKnown = false;
  let bufferAtTail = true;
  if (buffer) {
    const viewportY = Number(buffer.viewportY);
    const baseY = Number(buffer.baseY);
    if (Number.isFinite(viewportY) && Number.isFinite(baseY)) {
      bufferKnown = true;
      bufferAtTail = baseY <= viewportY;
    }
  }
  const viewport = _embeddedTerminalViewport(entry);
  const viewportAtTail = _embeddedTerminalViewportAtTail(viewport);
  return bufferKnown ? (bufferAtTail && viewportAtTail) : viewportAtTail;
}

function _embeddedTerminalTailPinned(entry) {
  if (!entry) return false;
  if (typeof entry.tailPinned === 'boolean') return entry.tailPinned;
  return _embeddedTerminalAtTail(entry);
}

function _updateEmbeddedTerminalTailButton(entry) {
  const button = entry && entry.tailButton;
  if (!button) return;
  const hidden = _embeddedTerminalTailPinned(entry) && _embeddedTerminalAtTail(entry);
  button.hidden = !!hidden;
  if (typeof button.setAttribute === 'function') {
    button.setAttribute('aria-hidden', hidden ? 'true' : 'false');
  }
}

function _embeddedTerminalSetTailPinned(entry, pinned) {
  if (!entry) return;
  entry.tailPinned = !!pinned;
  _updateEmbeddedTerminalTailButton(entry);
}

function _embeddedTerminalMarkUserScrollIntent(entry) {
  if (!entry) return;
  entry.userScrollIntentUntil = Date.now() + EMBEDDED_TERMINAL_USER_SCROLL_INTENT_MS;
}

function _embeddedTerminalHasUserScrollIntent(entry) {
  if (!entry) return false;
  if (entry.userScrollPointerActive) return true;
  return Number(entry.userScrollIntentUntil || 0) >= Date.now();
}

function _syncEmbeddedTerminalTailPinnedFromViewport(entry, userInitiated) {
  if (!entry) return;
  if (_embeddedTerminalAtTail(entry)) {
    _embeddedTerminalSetTailPinned(entry, true);
  } else if (userInitiated || _embeddedTerminalHasUserScrollIntent(entry)) {
    _embeddedTerminalSetTailPinned(entry, false);
  } else {
    _updateEmbeddedTerminalTailButton(entry);
  }
}

function _ensureEmbeddedTerminalTailButton(entry) {
  const surface = entry && entry.surface;
  if (!surface || !document.createElement) return null;
  let button = entry.tailButton || null;
  if (!button || button.parentNode !== surface) {
    button = document.createElement('button');
    button.type = 'button';
    button.className = 'terminal-tail-button';
    if (button.classList && typeof button.classList.add === 'function') {
      button.classList.add('terminal-tail-button');
    }
    button.title = 'Scroll to bottom';
    button.textContent = '↓';
    if (typeof button.setAttribute === 'function') {
      button.setAttribute('aria-label', 'Scroll terminal to bottom');
    }
    if (typeof button.addEventListener === 'function') {
      button.addEventListener('click', function(event) {
        if (event && typeof event.preventDefault === 'function') event.preventDefault();
        if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
        _embeddedTerminalScrollToTail(entry);
        if (entry && entry.sessionKey) _embeddedTerminalPendingFocusKey = entry.sessionKey;
        focusEmbeddedTerminalWorkspace(false);
      });
    }
    if (typeof surface.appendChild === 'function') surface.appendChild(button);
    entry.tailButton = button;
  }
  _updateEmbeddedTerminalTailButton(entry);
  return button;
}

function _detachEmbeddedTerminalTailControls(entry) {
  const controls = entry && entry.tailControls;
  if (!controls) return;
  const viewport = controls.viewport;
  const surface = controls.surface;
  const handlers = controls.handlers || {};
  if (viewport && typeof viewport.removeEventListener === 'function') {
    viewport.removeEventListener('wheel', handlers.userIntent);
    viewport.removeEventListener('touchstart', handlers.userIntent);
    viewport.removeEventListener('pointerdown', handlers.pointerDown);
    viewport.removeEventListener('scroll', handlers.scroll);
  }
  if (surface && typeof surface.removeEventListener === 'function') {
    surface.removeEventListener('keydown', handlers.userIntentKey);
  }
  if (typeof document !== 'undefined' && document && typeof document.removeEventListener === 'function') {
    document.removeEventListener('pointerup', handlers.pointerUp);
    document.removeEventListener('pointercancel', handlers.pointerUp);
  }
  entry.tailControls = null;
}

function _attachEmbeddedTerminalTailControls(entry) {
  if (!entry) return;
  _ensureEmbeddedTerminalTailButton(entry);
  const viewport = _embeddedTerminalViewport(entry);
  if (!viewport || typeof viewport.addEventListener !== 'function') {
    _updateEmbeddedTerminalTailButton(entry);
    return;
  }
  const surface = entry.surface || null;
  if (entry.tailControls && entry.tailControls.viewport === viewport
      && entry.tailControls.surface === surface) {
    _updateEmbeddedTerminalTailButton(entry);
    return;
  }
  _detachEmbeddedTerminalTailControls(entry);
  const userIntent = function() {
    _embeddedTerminalMarkUserScrollIntent(entry);
    if (typeof setTimeout === 'function') {
      setTimeout(function() {
        _syncEmbeddedTerminalTailPinnedFromViewport(entry, false);
      }, 0);
    }
  };
  const userIntentKey = function(event) {
    const key = event && (event.key || event.code);
    if (key === 'PageUp' || key === 'PageDown' || key === 'Home' || key === 'End'
        || key === 'ArrowUp' || key === 'ArrowDown' || key === 'Up' || key === 'Down') {
      userIntent();
    }
  };
  const pointerDown = function() {
    entry.userScrollPointerActive = true;
    _embeddedTerminalMarkUserScrollIntent(entry);
  };
  const pointerUp = function() {
    entry.userScrollPointerActive = false;
    _syncEmbeddedTerminalTailPinnedFromViewport(entry, true);
  };
  const scroll = function() {
    _syncEmbeddedTerminalTailPinnedFromViewport(entry, false);
  };
  viewport.addEventListener('wheel', userIntent, { passive: true });
  viewport.addEventListener('touchstart', userIntent, { passive: true });
  viewport.addEventListener('pointerdown', pointerDown, true);
  viewport.addEventListener('scroll', scroll);
  if (surface && typeof surface.addEventListener === 'function') {
    surface.addEventListener('keydown', userIntentKey, true);
  }
  if (typeof document !== 'undefined' && document && typeof document.addEventListener === 'function') {
    document.addEventListener('pointerup', pointerUp, true);
    document.addEventListener('pointercancel', pointerUp, true);
  }
  entry.tailControls = {
    viewport: viewport,
    surface: surface,
    handlers: {
      userIntent: userIntent,
      userIntentKey: userIntentKey,
      pointerDown: pointerDown,
      pointerUp: pointerUp,
      scroll: scroll,
    },
  };
  _updateEmbeddedTerminalTailButton(entry);
}

function _embeddedTerminalTailSnapshot(entry) {
  const term = entry && entry.terminal;
  const buffer = term && term.buffer && term.buffer.active;
  const viewportY = buffer ? Number(buffer.viewportY) : NaN;
  const viewport = _embeddedTerminalViewport(entry);
  return {
    atTail: _embeddedTerminalTailPinned(entry),
    viewportY: Number.isFinite(viewportY) ? viewportY : null,
    scrollTop: viewport ? (Number(viewport.scrollTop) || 0) : null,
  };
}

function _embeddedTerminalStillPinned(entry, snapshot) {
  if (!snapshot || !snapshot.atTail) return false;
  if (!_embeddedTerminalTailPinned(entry)) return false;
  const term = entry && entry.terminal;
  const buffer = term && term.buffer && term.buffer.active;
  if (buffer && snapshot.viewportY !== null) {
    const viewportY = Number(buffer.viewportY);
    if (Number.isFinite(viewportY)) return viewportY >= snapshot.viewportY;
  }
  const viewport = _embeddedTerminalViewport(entry);
  if (viewport && snapshot.scrollTop !== null) {
    return (Number(viewport.scrollTop) || 0) >= snapshot.scrollTop;
  }
  return true;
}

function _embeddedTerminalScrollToTail(entry) {
  const term = entry && entry.terminal;
  if (term && typeof term.scrollToBottom === 'function') {
    term.scrollToBottom();
  } else {
    const viewport = _embeddedTerminalViewport(entry);
    if (viewport) {
      viewport.scrollTop = Math.max(
        0,
        (Number(viewport.scrollHeight) || 0) - (Number(viewport.clientHeight) || 0)
      );
    }
  }
  _embeddedTerminalSetTailPinned(entry, true);
  const viewport = _embeddedTerminalViewport(entry);
  if (viewport) {
    viewport.scrollTop = Math.max(
      0,
      (Number(viewport.scrollHeight) || 0) - (Number(viewport.clientHeight) || 0)
    );
  }
  _updateEmbeddedTerminalTailButton(entry);
}

function _scheduleEmbeddedTerminalScrollToTail(entry) {
  if (!entry) return;
  // Defer past the fit() that activation also schedules so the scroll lands
  // after xterm has reflowed to the current stage size. requestAnimationFrame
  // callbacks fire in order, so the fit runs first.
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(function() {
      if (!_isEmbeddedTerminalEntryActive(entry)) return;
      _embeddedTerminalScrollToTail(entry);
    });
  } else {
    _embeddedTerminalScrollToTail(entry);
  }
}

function _writeEmbeddedTerminalData(entry, data) {
  const term = entry && entry.terminal;
  if (!term || !data || typeof term.write !== 'function') return;
  const tailSnapshot = _embeddedTerminalTailSnapshot(entry);
  let restored = false;
  function restoreTailIfPinned() {
    if (restored) return;
    restored = true;
    if (_embeddedTerminalStillPinned(entry, tailSnapshot)) {
      _embeddedTerminalScrollToTail(entry);
    } else {
      _updateEmbeddedTerminalTailButton(entry);
    }
  }
  term.write(data, restoreTailIfPinned);
  if (term.write.length < 2 && typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(restoreTailIfPinned);
  }
}

function _sanitizeEmbeddedTerminalRekeySnapshot(data) {
  return String(data || '')
    // TUIs often start a fresh session with a hard reset and display-clear
    // sequence. During a same-cell re-key those clears would erase the
    // preserved scrollback we intentionally kept, so suppress them only for
    // the first snapshot after the session swap.
    .replace(/\x1bc/g, '')
    .replace(/\x1b\[[0-?]*[ -/]*J/g, '');
}

function _scheduleEmbeddedTerminalFit(entry, opts) {
  entry = entry || _embeddedTerminalSessions[_embeddedTerminalSessionKey];
  if (!entry || !_isEmbeddedTerminalEntryActive(entry)
      || !entry.terminal || !entry.fit) return;
  const preserveTail = !!(opts && opts.preserveTail);
  requestAnimationFrame(function() {
    if (!_isEmbeddedTerminalEntryActive(entry) || !entry.terminal || !entry.fit) return;
    // Capture tail state from the logical buffer before the fit reflows xterm.
    // The compose box growing to multiple lines shrinks the terminal stage and
    // fires the surface ResizeObserver; without re-pinning, the reflow detaches
    // the viewport from the bottom and tail/autoscroll silently stops while the
    // user is still typing. Only re-pin when the user was already at the tail so
    // a deliberate scroll-up survives the resize.
    const wasAtTail = preserveTail && _embeddedTerminalTailPinned(entry);
    entry.fit.fit();
    _attachEmbeddedTerminalTailControls(entry);
    if (wasAtTail) _embeddedTerminalScrollToTail(entry);
    else _updateEmbeddedTerminalTailButton(entry);
    const cols = entry.terminal.cols;
    const rows = entry.terminal.rows;
    if (entry.lastSentCols === cols && entry.lastSentRows === rows) return;
    if (entry.ws && entry.ws.readyState === WebSocket.OPEN) {
      entry.ws.send(JSON.stringify({
        type: 'resize',
        cols: cols,
        rows: rows,
      }));
      entry.ws.send(JSON.stringify({ type: 'focus' }));
      entry.lastSentCols = cols;
      entry.lastSentRows = rows;
    }
  });
}

function _embeddedTerminalObservedSizeChanged(entry, resizeEntries) {
  var roEntry = resizeEntries && resizeEntries.length ? resizeEntries[0] : null;
  var box = roEntry && roEntry.contentBoxSize;
  if (Array.isArray(box)) box = box[0];
  var width = box && typeof box.inlineSize === 'number'
    ? box.inlineSize : roEntry && roEntry.contentRect && roEntry.contentRect.width;
  var height = box && typeof box.blockSize === 'number'
    ? box.blockSize : roEntry && roEntry.contentRect && roEntry.contentRect.height;
  if (typeof width !== 'number' || typeof height !== 'number') return true;
  var changed = entry.lastObservedWidth !== width || entry.lastObservedHeight !== height;
  entry.lastObservedWidth = width;
  entry.lastObservedHeight = height;
  return changed;
}

// Max attempts to re-open the terminal WS after a drop (e.g. daemon
// restart). Each attempt sleeps _EMBEDDED_TERMINAL_RETRY_MS between
// tries, giving ~15s of cover for a standalone-mode restart.
var _EMBEDDED_TERMINAL_RETRY_MS = 1000;
var _EMBEDDED_TERMINAL_MAX_RETRIES = 15;

function _connectEmbeddedTerminal(cell, surface) {
  var expectedSessionId = cell.session_id || '';
  var sessionKey = cell.id + ':' + expectedSessionId;
  var existingEntry = _embeddedTerminalSessions[sessionKey]
    || _findEmbeddedTerminalEntryForCell(cell.id);
  if (existingEntry) {
    var oldSessionKey = existingEntry.sessionKey || '';
    var sessionChanged = oldSessionKey !== sessionKey
      || (existingEntry.sessionId || '') !== expectedSessionId;
    _closeEmbeddedTerminalEntrySocket(existingEntry);
    _updateEmbeddedTerminalEntrySession(existingEntry, cell, sessionKey, expectedSessionId);
    _disposeEmbeddedTerminalEntriesForCell(cell.id, sessionKey);
    if (surface && existingEntry.surface && surface !== existingEntry.surface
        && typeof surface.remove === 'function') {
      surface.remove();
    }
    if (sessionChanged) {
      _writeEmbeddedTerminalSessionRestartedSeparator(existingEntry);
      existingEntry.appendNextSnapshot = true;
    } else {
      existingEntry.appendNextSnapshot = false;
    }
    _embeddedTerminalPendingFocusKey = sessionKey;
    _setActiveEmbeddedTerminalEntry(existingEntry);
    _openEmbeddedTerminalSocket(cell, sessionKey, expectedSessionId, 0, existingEntry);
    _scheduleEmbeddedTerminalFit(existingEntry);
    return;
  }
  var entry = {
    sessionKey: sessionKey,
    cellId: cell.id,
    sessionId: expectedSessionId,
    surface: surface,
    tailPinned: true,
  };
  _embeddedTerminalSessions[sessionKey] = entry;
  _embeddedTerminalPendingFocusKey = sessionKey;
  entry.terminal = new Terminal({
    allowProposedApi: true,
    allowTransparency: false,
    convertEol: false,
    cursorBlink: true,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    fontSize: 13,
    lineHeight: 1.0,
    letterSpacing: 0,
    scrollback: _currentXtermScrollback(),
    theme: {
      background: '#0d1117',
      foreground: '#e6edf3',
      cursor: '#58a6ff',
      selectionBackground: 'rgba(88,166,255,0.28)',
    },
  });
  entry.fit = new FitAddon.FitAddon();
  entry.terminal.loadAddon(entry.fit);
  entry.terminal.open(surface);
  _setActiveEmbeddedTerminalEntry(entry);
  _attachEmbeddedTerminalTailControls(entry);
  _attachEmbeddedTerminalDropHandlers(cell, surface, entry);
  try { entry.fit.fit(); } catch (e) { /* container not measurable yet */ }
  // Shift+Enter → send LF so TUIs like Codex and Claude Code treat it as a
  // soft newline instead of submitting. xterm.js default maps Shift+Enter
  // to plain Enter; consume every matching key event so the follow-up keypress
  // cannot emit a stray `\r`.
  if (typeof entry.terminal.attachCustomKeyEventHandler === 'function') {
    entry.terminal.attachCustomKeyEventHandler(function(e) {
      if (e.key === 'Enter' && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (e.type === 'keydown' && entry.ws && entry.ws.readyState === WebSocket.OPEN) {
          entry.ws.send(JSON.stringify({ type: 'input', data: '\n' }));
        }
        return false;
      }
      if (e.type !== 'keydown') return true;
      return true;
    });
  }
  entry.dataHandler = entry.terminal.onData(function(data) {
    if (entry.ws && entry.ws.readyState === WebSocket.OPEN) {
      entry.ws.send(JSON.stringify({ type: 'input', data: data }));
    }
  });
  entry.resizeObserver = new ResizeObserver(function(entries) {
    if (!_embeddedTerminalObservedSizeChanged(entry, entries)) return;
    _scheduleEmbeddedTerminalFit(entry, { preserveTail: true });
  });
  entry.resizeObserver.observe(surface);
  _setActiveEmbeddedTerminalEntry(entry);
  _openEmbeddedTerminalSocket(cell, sessionKey, expectedSessionId, 0, entry);
  _scheduleEmbeddedTerminalFit(entry);
}

function _openEmbeddedTerminalSocket(cell, sessionKey, expectedSessionId, attempt, entry) {
  if (_embeddedTerminalSessions[sessionKey] !== entry) return;
  var socket = new WebSocket(_embeddedTerminalUrl(cell));
  entry.ws = socket;
  entry.lastSentCols = null;
  entry.lastSentRows = null;
  if (_isEmbeddedTerminalEntryActive(entry)) _embeddedTerminalWs = socket;
  function isCurrentSessionMessage(msg) {
    if (_embeddedTerminalSessions[sessionKey] !== entry) return false;
    if (entry.ws !== socket) return false;
    if (msg && typeof msg.session_id === 'string' && msg.session_id !== expectedSessionId) {
      return false;
    }
    return true;
  }
  socket.onopen = function() {
    if (!isCurrentSessionMessage()) return;
    if (_isEmbeddedTerminalEntryActive(entry)) {
      var status = document.querySelector('#terminal-workspace .terminal-statusbar');
      if (status && typeof status.removeAttribute === 'function') {
        status.removeAttribute('data-closed');
      }
      _scheduleEmbeddedTerminalFit(entry);
      focusEmbeddedTerminalWorkspace(false);
    }
  };
  socket.onmessage = function(event) {
    var msg;
    try { msg = JSON.parse(event.data); } catch (e) { return; }
    if (!isCurrentSessionMessage(msg)) return;
    if (msg.type === 'snapshot') {
      if (entry.appendNextSnapshot) {
        entry.appendNextSnapshot = false;
        if (msg.data) msg.data = _sanitizeEmbeddedTerminalRekeySnapshot(msg.data);
      } else {
        entry.terminal.reset();
      }
      if (msg.data) _writeEmbeddedTerminalData(entry, msg.data);
      if (_isEmbeddedTerminalEntryActive(entry)) {
        _scheduleEmbeddedTerminalFit(entry);
        focusEmbeddedTerminalWorkspace(false);
      }
    } else if (msg.type === 'output' && msg.data) {
      _writeEmbeddedTerminalData(entry, msg.data);
    }
  };
  socket.onclose = function() {
    if (_embeddedTerminalSessions[sessionKey] !== entry) return;
    if (entry.ws !== socket) return;
    if (_isEmbeddedTerminalEntryActive(entry)) {
      var status = document.querySelector('#terminal-workspace .terminal-statusbar');
      if (status) status.setAttribute('data-closed', '1');
    }
    // The main `/ws` socket delivers state updates out-of-band; check
    // whether the session is still the one the current cell points to.
    // If the cell's session_id changed (stopped, relaunched to a new id),
    // bail out — renderTerminalWorkspace() will set up a fresh surface.
    if (state && state.agents && state.agents[cell.id]) {
      var currentSid = state.agents[cell.id].session_id || '';
      if (currentSid !== expectedSessionId) return;
    } else {
      return;
    }
    if (attempt >= _EMBEDDED_TERMINAL_MAX_RETRIES) return;
    setTimeout(function() {
      if (_embeddedTerminalSessions[sessionKey] !== entry) return;
      _openEmbeddedTerminalSocket(cell, sessionKey, expectedSessionId, attempt + 1, entry);
    }, _EMBEDDED_TERMINAL_RETRY_MS);
  };
  socket.onerror = function() { /* close will fire after */ };
}

function _pruneEmbeddedTerminalSessions() {
  if (!state || !state.agents) return;
  for (const key of Object.keys(_embeddedTerminalSessions)) {
    const entry = _embeddedTerminalSessions[key];
    const cell = entry && state.agents[entry.cellId];
    if (!cell || _terminalCellIsTombstoned(cell)) {
      _disposeEmbeddedTerminalEntry(entry);
    }
  }
}

function _clearEmbeddedTerminalStagePlaceholders(stage) {
  if (!stage || !stage.children) return;
  const children = Array.prototype.slice.call(stage.children);
  for (let i = 0; i < children.length; i++) {
    const child = children[i];
    if (child && child.classList && child.classList.contains('terminal-surface')) continue;
    if (child && typeof child.remove === 'function') {
      child.remove();
    } else if (stage && typeof stage.removeChild === 'function') {
      stage.removeChild(child);
    }
  }
}

function _createEmbeddedTerminalSurface(stage, sessionKey) {
  _clearEmbeddedTerminalStagePlaceholders(stage);
  let surface = stage && stage.querySelector ? stage.querySelector('.terminal-surface') : null;
  if (surface && surface.dataset && surface.dataset.torqueSessionKey) surface = null;
  if (!surface) {
    surface = document.createElement('div');
    if (stage && typeof stage.appendChild === 'function') stage.appendChild(surface);
  }
  surface.className = 'terminal-surface';
  if (surface.classList && typeof surface.classList.add === 'function') {
    surface.classList.add('terminal-surface');
  }
  if (surface.dataset) surface.dataset.torqueSessionKey = sessionKey;
  if (typeof surface.setAttribute === 'function') {
    surface.setAttribute('data-torque-session-key', sessionKey);
  }
  return surface;
}

function _activateEmbeddedTerminalSurface(stage, sessionKey, opts) {
  opts = opts || {};
  _clearEmbeddedTerminalStagePlaceholders(stage);
  const entry = _embeddedTerminalSessions[sessionKey] || null;
  // The previously active session, captured before _setActiveEmbeddedTerminalEntry
  // overwrites it, so we can tell an agent switch apart from a same-session
  // rerender (renderTerminalWorkspace runs on every grid render).
  const previousSessionKey = _embeddedTerminalSessionKey;
  for (const key in _embeddedTerminalSessions) {
    const candidate = _embeddedTerminalSessions[key];
    if (!candidate || !candidate.surface) continue;
    const active = key === sessionKey;
    if (active && stage && candidate.surface.parentNode !== stage
        && typeof stage.appendChild === 'function') {
      stage.appendChild(candidate.surface);
    }
    candidate.surface.hidden = !active;
    if (candidate.surface.style) candidate.surface.style.display = active ? '' : 'none';
  }
  _setActiveEmbeddedTerminalEntry(entry);
  if (entry) {
    _attachEmbeddedTerminalTailControls(entry);
    _scheduleEmbeddedTerminalFit(entry, { preserveTail: !!opts.preserveTail });
    // Switching to an agent's terminal should land at the bottom and resume
    // tailing rather than leaving the viewport pinned to the top (or wherever
    // the previous activation left it). Skip same-session rerenders so a
    // deliberate scroll-up is preserved during normal output.
    if (previousSessionKey !== sessionKey) {
      _scheduleEmbeddedTerminalScrollToTail(entry);
    }
  }
  return entry;
}

function _renderEmbeddedTerminalStagePlaceholder(stage, html) {
  if (!stage) return;
  const hasPlaceholder = !!(stage.children && Array.prototype.some.call(stage.children, function(child) {
    return !(child && child.classList && child.classList.contains('terminal-surface'));
  }));
  if (stage._torqueLastHtml === html && hasPlaceholder) return;
  _clearEmbeddedTerminalStagePlaceholders(stage);
  const placeholder = document.createElement('div');
  placeholder.className = 'terminal-empty';
  if (placeholder.classList && typeof placeholder.classList.add === 'function') {
    placeholder.classList.add('terminal-empty');
  }
  placeholder.innerHTML = html;
  if (typeof stage.appendChild === 'function') stage.appendChild(placeholder);
  stage._torqueLastHtml = html;
}

function renderTerminalWorkspace(opts) {
  opts = opts || {};
  const root = document.getElementById('terminal-workspace');
  if (!root) return;
  _terminalComposePersistFromDom(root);
  if (!isEmbeddedTerminalMode()) {
    root.innerHTML = '';
    root.classList.remove('active');
    _disposeEmbeddedTerminal();
    return;
  }
  root.classList.add('active');
  _pruneEmbeddedTerminalSessions();
  const group = _terminalCurrentGroupName();
  const groupLabel = group || '';
  const cells = _terminalGroupCells(group);
  const cell = _resolveTerminalWorkspaceCell();
  const agentTarget = _terminalTargetAgent(cell);
  const relaunchAction = cell && !cell.session_id ? {
    label: 'Relaunch',
    onclick: 'relaunchAgent(\'' + esc(cell.id) + '\')',
  } : null;
  const topbarAction = cell && cell.cell_type !== 'terminal' && !cell.session_id
    ? relaunchAction
    : null;
  const displayPath = _terminalDisplayPath(cell);
  const dom = _ensureTerminalWorkspaceDom(root);
  const workspaceState = _captureTerminalWorkspaceState(root, cell);
  const preserveTerminalTailOnFit = _terminalWorkspaceFocusedComposeHasDraft(root);
  if (opts.suppressTerminalFocus && workspaceState && workspaceState.focus
      && _terminalDirectMessageFocusIsTerminal(root)) {
    workspaceState.focus = null;
  }
  const title = cell && cell.name ? cell.name : 'Terminal';
  // Idempotent topbar: skip the innerHTML clobber when the rendered
  // HTML hasn't changed. Under multi-agent activity `renderTerminalWorkspace`
  // is called on every grid render (TORQUE:264 firehose) — without this guard
  // we rewrite the topbar DOM dozens of times per second even though nothing
  // visible changed.
  const topbarHtml = ''
    + '<div class="terminal-topbar-left">'
    + '  <span class="terminal-title">' + esc(title) + '</span>'
    + '  <span class="terminal-group-pill">' + esc(groupLabel || 'Standalone') + '</span>'
    + '</div>'
    + '<div class="terminal-topbar-right">'
    + (topbarAction
      ? '  <button class="terminal-topbar-btn terminal-topbar-btn-primary" onclick="' + topbarAction.onclick + '">' + topbarAction.label + '</button>'
      : '')
    + '</div>';
  if (dom.topbar._torqueLastHtml !== topbarHtml) {
    dom.topbar.innerHTML = topbarHtml;
    dom.topbar._torqueLastHtml = topbarHtml;
  }
  if (dom.tabs) {
    dom.tabs.classList.toggle('terminal-tabs-hidden', true);
    if (dom.tabs._torqueLastHtml !== '') {
      dom.tabs.innerHTML = '';
      dom.tabs._torqueLastHtml = '';
    }
  }

  if (!cell) {
    _renderTerminalDirectMessages(dom.directMessages, null);
    _renderTerminalCompose(dom.compose, null);
    const emptyHtml = ''
      + '<div class="terminal-empty">'
      + '  <div class="terminal-empty-title">Select an agent</div>'
      + '  <div class="terminal-empty-body">Choose an agent, worker, engineer, architect, or legacy terminal from the grid to view its session here.</div>'
      + '  <div class="terminal-empty-meta">Manual terminal creation has moved out of the operator UI.</div>'
      + '</div>';
    if (dom.stage._torqueLastHtml !== emptyHtml) {
      dom.stage.innerHTML = emptyHtml;
      dom.stage._torqueLastHtml = emptyHtml;
    }
    if (dom.statusbar.textContent !== 'Standalone PTY workspace') {
      dom.statusbar.textContent = 'Standalone PTY workspace';
    }
    _deactivateEmbeddedTerminalWorkspace();
    _restoreTerminalWorkspaceState(root, workspaceState, null);
    return;
  }

  const sessionKey = cell.id + ':' + (cell.session_id || '');
  if (!cell.session_id) {
    _renderTerminalDirectMessages(dom.directMessages, cell);
    _renderTerminalCompose(dom.compose, cell);
    const stoppedHtml = cell.cell_type === 'terminal'
      ? ''
        + '  <div class="terminal-empty-title">' + esc(cell.name) + ' is stopped</div>'
        + '  <div class="terminal-empty-body">This legacy terminal remains available in the grid so you can inspect or delete it, but manual terminal relaunch is no longer available from the UI.</div>'
        + '  <div class="terminal-empty-meta">Select an agent card to switch the workspace to an active session.</div>'
      : ''
        + '  <div class="terminal-empty-title">' + esc(cell.name) + ' is stopped</div>'
        + '  <div class="terminal-empty-body">Relaunch this session to put it back in the workspace and return keyboard focus to the shell.</div>'
        + '  <button class="terminal-empty-btn" onclick="relaunchAgent(\'' + esc(cell.id) + '\')">Relaunch</button>'
        + '  <div class="terminal-empty-meta">When it comes back, Torque will focus the terminal automatically.</div>';
    const stoppedEntry = _findEmbeddedTerminalEntryForCell(cell.id);
    if (stoppedEntry) {
      _activateEmbeddedTerminalSurface(dom.stage, stoppedEntry.sessionKey);
      dom.stage._torqueLastHtml = null;
    } else {
      _activateEmbeddedTerminalSurface(dom.stage, sessionKey, {
        preserveTail: preserveTerminalTailOnFit,
      });
      _renderEmbeddedTerminalStagePlaceholder(dom.stage, stoppedHtml);
    }
    const statusLabel = _terminalStatusLabel(cell);
    if (dom.statusbar.textContent !== statusLabel) {
      dom.statusbar.textContent = statusLabel;
    }
    if (!stoppedEntry) _deactivateEmbeddedTerminalWorkspace();
    _restoreTerminalWorkspaceState(root, workspaceState, cell);
    return;
  }

  let entry = _embeddedTerminalSessions[sessionKey] || null;
  if (!entry) {
    const reusableEntry = _findEmbeddedTerminalEntryForCell(cell.id);
    const surface = reusableEntry && reusableEntry.surface
      ? reusableEntry.surface
      : _createEmbeddedTerminalSurface(dom.stage, sessionKey);
    _connectEmbeddedTerminal(cell, surface);
    entry = _embeddedTerminalSessions[sessionKey] || null;
  } else {
    _applyEmbeddedTerminalScrollbackFromSettings();
  }
  _activateEmbeddedTerminalSurface(dom.stage, sessionKey, {
    preserveTail: preserveTerminalTailOnFit,
  });
  // The active branch attaches/toggles xterm surfaces directly on the stage
  // rather than rewriting `dom.stage.innerHTML`. Invalidate the empty/stopped
  // HTML cache so the next transition back to a no-cell / stopped-cell state
  // re-renders the empty placeholder.
  dom.stage._torqueLastHtml = null;

  _renderTerminalDirectMessages(dom.directMessages, cell);
  _renderTerminalCompose(dom.compose, cell);
  const statusText = (displayPath || 'No directory') + '  |  ' + _terminalStatusLabel(cell);
  if (dom.statusbar.textContent !== statusText) dom.statusbar.textContent = statusText;
  const statusTitle = cell.current_path || cell.directory || '';
  if (dom.statusbar.title !== statusTitle) dom.statusbar.title = statusTitle;
  _restoreTerminalWorkspaceState(root, workspaceState, cell);
}

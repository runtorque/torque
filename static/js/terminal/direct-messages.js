/* Terminal module: direct messages. */

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

function _terminalAgentMessageLoopsForAgent(agentId) {
  const id = String(agentId || '').trim();
  const loops = state && state.agent_message_loops && typeof state.agent_message_loops === 'object'
    ? state.agent_message_loops
    : {};
  return Object.keys(loops).map(function(key) {
    return Object.assign({}, loops[key] || {});
  }).filter(function(loop) {
    return String(loop.agent_id || '').trim() === id;
  }).sort(function(a, b) {
    const at = Number(a.updated_at || a.created_at || 0) || 0;
    const bt = Number(b.updated_at || b.created_at || 0) || 0;
    if (at !== bt) return bt - at;
    return String(a.id || '').localeCompare(String(b.id || ''));
  });
}

function _terminalActiveAgentMessageLoop(agentId) {
  const rows = _terminalAgentMessageLoopsForAgent(agentId);
  for (let i = 0; i < rows.length; i++) {
    if (String(rows[i].status || '').trim() === 'active') return rows[i];
  }
  return null;
}

function _terminalLoopIntervalLabel(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds || 0) || 0));
  if (value && value % 3600 === 0) return (value / 3600) + 'h';
  if (value && value % 60 === 0) return (value / 60) + 'm';
  return value + 's';
}

function _terminalLoopTimeLabel(secondsValue) {
  const ts = Number(secondsValue || 0) || 0;
  if (!ts) return '';
  try {
    return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch (_e) {
    return '';
  }
}

function _terminalLoopPreview(text) {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  if (!value) return 'message';
  return value.length > 96 ? value.slice(0, 93) + '\u2026' : value;
}

function _renderTerminalAgentMessageLoopHtml(agent) {
  const loop = _terminalActiveAgentMessageLoop(agent && agent.id);
  if (!loop) return '';
  const agentId = String((agent && agent.id) || '');
  const nextLabel = _terminalLoopTimeLabel(loop.next_run_at);
  const isDeferred = Number(loop.deferred_at || 0) > 0
    && String(loop.deferred_reason || '').trim() === 'agent_busy';
  const deliveryLabel = isDeferred
    ? ' · deferred until idle'
    : (nextLabel ? ' · next ' + esc(nextLabel) : '');
  return ''
    + '<div class="terminal-direct-loop" data-loop-id="' + esc(loop.id || '') + '">'
    + '  <div class="terminal-direct-loop-main">'
    + '    <span class="terminal-direct-loop-badge">/loop</span>'
    + '    <span class="terminal-direct-loop-text">Every ' + esc(_terminalLoopIntervalLabel(loop.interval_seconds))
    + deliveryLabel
    + ' · ' + esc(_terminalLoopPreview(loop.message)) + '</span>'
    + '  </div>'
    + '  <button type="button" class="terminal-direct-loop-cancel"'
    + ' onclick="return terminalCancelUserMessageLoop(event, \'' + esc(agentId) + '\')">'
    + 'Cancel</button>'
    + '</div>';
}

function _terminalDirectMessageBodyHtml(row) {
  const text = _terminalDirectMessageText(row);
  let html = '';
  if (typeof torqueRenderMarkdownMessage === 'function') {
    html = torqueRenderMarkdownMessage(text);
  } else {
    html = esc(text).replace(/\n/g, '<br>');
  }
  return _terminalDirectMessageEnhanceCodeBlocks(html);
}

function _terminalDirectMessageEnhanceCodeBlocks(html) {
  return String(html || '').replace(
    /<pre class="torque-md-code-block"><code>([\s\S]*?)<\/code><\/pre>/g,
    function(_match, codeHtml) {
      return '<div class="terminal-direct-message-code-block">'
        + '<button type="button" class="terminal-direct-message-code-copy"'
        + ' onmousedown="return terminalDirectMessageCopyCodeBlockMouseDown(event)"'
        + ' onclick="return terminalDirectMessageCopyCodeBlock(event)">Copy</button>'
        + '<pre class="torque-md-code-block"><code>' + codeHtml + '</code></pre>'
        + '</div>';
    }
  );
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
    + (type === 'system' ? ' terminal-direct-message--status-card' : '')
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
    + _renderTerminalAgentMessageLoopHtml(agent)
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
  // Browser DOM serialization is not byte-stable with the generated string:
  // whitespace/boolean attributes may normalize after the first assignment.
  // PR #809 made the composer itself idempotent, but this `innerHTML !== html`
  // fallback still rewrote the entire Direct Messages slot on every
  // main-surface render while an agent was outputting. That below-terminal DOM
  // rewrite can perturb layout, fire xterm's ResizeObserver/fit path, and make
  // the terminal visibly flash while the user types in the DM composer even
  // though neither the selected session nor the message list changed. Trust the
  // explicit render cache for this owned slot; if the node is recreated, its
  // cache is recreated too.
  if (root._torqueLastHtml !== html) {
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
  if (typeof openContextMenuSurface === 'function') {
    openContextMenuSurface(menu, { invoker: event && event.currentTarget });
  } else if (menu.classList) {
    menu.classList.add('open');
  }
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

function terminalDirectMessageCopyCodeBlockMouseDown(event) {
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  return true;
}

function terminalDirectMessageCopyCodeBlock(event) {
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  const target = event && (event.currentTarget || event.target);
  const wrapper = target && typeof target.closest === 'function'
    ? target.closest('.terminal-direct-message-code-block')
    : null;
  const code = wrapper && typeof wrapper.querySelector === 'function'
    ? wrapper.querySelector('pre code')
    : null;
  const text = code ? String(code.textContent || '') : '';
  const clipboard = (typeof navigator !== 'undefined' && navigator) ? navigator.clipboard : null;
  if (clipboard && typeof clipboard.writeText === 'function') {
    try {
      clipboard.writeText(text);
    } catch (_e) {}
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

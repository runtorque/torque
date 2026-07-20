/* Durable operator Inbox: alerts require resolution; notifications require
 * acknowledgement. Transient overlays are only a delivery surface for these
 * persisted records. */

var _inboxView = 'alerts';
var _inboxShowArchived = false;
var _inboxHistoryRequested = false;
var _inboxHasMore = false;
var _inboxHistoryOffset = 0;
var _inboxPopoverAnchor = null;

function _inboxNoticeMap() {
  if (!state.operator_notices || typeof state.operator_notices !== 'object') {
    state.operator_notices = {};
  }
  return state.operator_notices;
}

function _inboxSummary() {
  var raw = state.operator_notice_summary;
  if (!raw || typeof raw !== 'object') raw = {};
  return {
    open_alerts: Number(raw.open_alerts || 0),
    unread_alerts: Number(raw.unread_alerts || 0),
    unread_notifications: Number(raw.unread_notifications || 0),
    unread_total: Number(raw.unread_total || 0),
    active_total: Number(raw.active_total || 0),
  };
}

function inboxNormalizeState() {
  _inboxNoticeMap();
  _inboxHistoryRequested = false;
  _inboxHistoryOffset = Object.keys(state.operator_notices).length;
  _inboxHasMore = false;
  if (!state.operator_notice_summary
      || typeof state.operator_notice_summary !== 'object') {
    state.operator_notice_summary = _inboxSummary();
  }
  inboxUpdateBadge();
}

function inboxUpdateBadge() {
  var summary = _inboxSummary();
  var count = summary.open_alerts + summary.unread_notifications;
  var buttons = document.querySelectorAll
    ? Array.prototype.slice.call(document.querySelectorAll('.inbox-bell-button'))
    : [];
  buttons.forEach(function(button) {
    button.classList.toggle('has-badge', count > 0);
    button.classList.toggle('inbox-has-alerts', summary.open_alerts > 0);
    button.setAttribute(
      'aria-label',
      count > 0
        ? 'Notifications, ' + count + ' item' + (count === 1 ? '' : 's') + ' need attention'
        : 'Notifications'
    );
    var badge = button.querySelector('.inbox-bell-badge');
    if (!badge) return;
    badge.hidden = count < 1;
    badge.textContent = count > 99 ? '99+' : String(count);
  });
}

function _inboxPopoverVisible() {
  var root = document.getElementById('inbox-popover');
  return !!(root && !root.hidden);
}

function _inboxConversationAgentId(cellId) {
  var id = String(cellId || '').trim();
  if (!id || !state || !state.agents) return '';
  var cell = state.agents[id];
  if (!cell) return '';
  if (cell.cell_type === 'agent') return String(cell.id || '').trim();
  if (cell.cell_type === 'terminal') {
    var parentId = String(cell.parent_id || '').trim();
    return parentId && state.agents[parentId] ? parentId : '';
  }
  return '';
}

function _inboxActiveConversationAgentId() {
  return _inboxConversationAgentId(
    typeof selectedTerminalId !== 'undefined' ? selectedTerminalId : ''
  );
}

function _inboxFocusedConversationAgentId() {
  return _inboxConversationAgentId(
    typeof focusedItemId !== 'undefined' ? focusedItemId : ''
  );
}

function _inboxDocumentVisibleAndFocused() {
  if (typeof document === 'undefined' || !document) return false;
  if (document.hidden || document.visibilityState === 'hidden') return false;
  // Suppression must fail closed: without a positive focus signal, retain the
  // normal Inbox/toast delivery rather than silently acknowledging a message.
  if (typeof document.hasFocus !== 'function') return false;
  try {
    return document.hasFocus() === true;
  } catch (_err) {
    return false;
  }
}

function _inboxActiveConversationVisible() {
  var root = document.getElementById('terminal-workspace');
  return !!(
    root
    && !root.hidden
    && root.classList
    && typeof root.classList.contains === 'function'
    && root.classList.contains('active')
  );
}

function _inboxShouldAcknowledgeFocusedDirectMessage(notice) {
  if (!notice
      || notice.notice_type !== 'notification'
      || String(notice.category || '') !== 'direct_message'
      || String(notice.severity || '').toLowerCase() === 'error') {
    return false;
  }
  var agentId = String(notice.agent_id || '').trim();
  return !!(
    agentId
    && _inboxDocumentVisibleAndFocused()
    && _inboxActiveConversationVisible()
    && _inboxActiveConversationAgentId() === agentId
    && _inboxFocusedConversationAgentId() === agentId
  );
}

function _inboxAcknowledgeFocusedDirectMessage(notice) {
  if (!notice || Number(notice.read_at || 0)) return;
  // Keep the local badge truthful while the normal lifecycle command persists
  // the acknowledgement and returns its authoritative summary/delta.
  notice.read_at = Date.now() / 1000;
  var summary = _inboxSummary();
  summary.unread_notifications = Math.max(0, summary.unread_notifications - 1);
  summary.unread_total = Math.max(0, summary.unread_total - 1);
  state.operator_notice_summary = summary;
  inboxLifecycle(notice.id, 'read');
}

function _inboxDefaultAnchor() {
  var buttons = document.querySelectorAll
    ? document.querySelectorAll('.inbox-bell-button')
    : [];
  for (var i = 0; i < buttons.length; i++) {
    var button = buttons[i];
    if (!button || button.hidden
        || typeof button.getBoundingClientRect !== 'function') continue;
    var rect = button.getBoundingClientRect();
    if (Number(rect.width || (rect.right - rect.left)) > 0
        && Number(rect.height || (rect.bottom - rect.top)) > 0) {
      return button;
    }
  }
  return buttons[0] || null;
}

function _positionInboxPopover() {
  var root = document.getElementById('inbox-popover');
  var anchor = _inboxPopoverAnchor;
  if (!root || root.hidden || !anchor
      || typeof anchor.getBoundingClientRect !== 'function') return;
  var rect = anchor.getBoundingClientRect();
  var viewportWidth = Number(window.innerWidth || 0);
  var viewportHeight = Number(window.innerHeight || 0);
  var width = Number(root.offsetWidth || 420);
  var height = Number(root.offsetHeight || 560);
  var gap = 6;
  var margin = 8;
  var left = Math.max(
    margin,
    Math.min(rect.right - width, viewportWidth - width - margin)
  );
  var below = rect.bottom + gap;
  var above = rect.top - height - gap;
  var top = below + height <= viewportHeight - margin
    ? below
    : Math.max(margin, above);
  root.style.left = left + 'px';
  root.style.top = top + 'px';
}

function openInboxPopover(event) {
  if (event) {
    if (typeof event.preventDefault === 'function') event.preventDefault();
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
  }
  var root = document.getElementById('inbox-popover');
  if (!root) return false;
  _inboxPopoverAnchor = (event && (event.currentTarget || event.target))
    || _inboxPopoverAnchor
    || _inboxDefaultAnchor();
  if (typeof closeNavigationMenus === 'function') closeNavigationMenus();
  root.hidden = false;
  root.classList.add('open');
  if (_inboxPopoverAnchor && _inboxPopoverAnchor.setAttribute) {
    _inboxPopoverAnchor.setAttribute('aria-expanded', 'true');
  }
  inboxEnsureLoaded();
  renderInbox();
  _positionInboxPopover();
  return true;
}

function closeInboxPopover(options) {
  options = options || {};
  var root = document.getElementById('inbox-popover');
  if (!root || root.hidden) return false;
  root.hidden = true;
  root.classList.remove('open');
  var buttons = document.querySelectorAll
    ? document.querySelectorAll('.inbox-bell-button')
    : [];
  Array.prototype.forEach.call(buttons, function(button) {
    if (button.setAttribute) button.setAttribute('aria-expanded', 'false');
  });
  if (options.restoreFocus && _inboxPopoverAnchor
      && typeof _inboxPopoverAnchor.focus === 'function') {
    _inboxPopoverAnchor.focus();
  }
  return true;
}

function toggleInboxPopover(event) {
  var anchor = event && (event.currentTarget || event.target);
  if (_inboxPopoverVisible() && (!anchor || anchor === _inboxPopoverAnchor)) {
    if (event) {
      if (typeof event.preventDefault === 'function') event.preventDefault();
      if (typeof event.stopPropagation === 'function') event.stopPropagation();
    }
    closeInboxPopover({ restoreFocus: false });
    return false;
  }
  return openInboxPopover(event);
}

function _inboxTimestamp(value) {
  var seconds = Number(value || 0);
  if (!seconds) return '';
  var elapsed = Math.max(0, Date.now() - seconds * 1000);
  if (elapsed < 60000) return 'now';
  if (elapsed < 3600000) return Math.floor(elapsed / 60000) + 'm ago';
  if (elapsed < 86400000) return Math.floor(elapsed / 3600000) + 'h ago';
  if (elapsed < 604800000) return Math.floor(elapsed / 86400000) + 'd ago';
  try {
    return new Date(seconds * 1000).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
    });
  } catch (_err) {
    return '';
  }
}

function _inboxNoticeState(notice) {
  if (Number(notice.archived_at || 0)) return 'Archived';
  if (notice.notice_type === 'alert') {
    if (Number(notice.resolved_at || 0)) return 'Resolved';
    if (Number(notice.dismissed_at || 0)) return 'Dismissed';
    return 'Open';
  }
  return Number(notice.read_at || 0) ? 'Read' : 'Unread';
}

function _inboxNoticeVisible(notice) {
  if (!notice || notice.notice_type !== (_inboxView === 'notifications'
      ? 'notification'
      : 'alert')) return false;
  return _inboxShowArchived || !Number(notice.archived_at || 0);
}

function _inboxSortedNotices() {
  return Object.keys(_inboxNoticeMap())
    .map(function(id) { return _inboxNoticeMap()[id]; })
    .filter(_inboxNoticeVisible)
    .sort(function(a, b) {
      var aActive = a.notice_type === 'alert'
        ? !Number(a.resolved_at || 0) && !Number(a.dismissed_at || 0)
        : !Number(a.read_at || 0);
      var bActive = b.notice_type === 'alert'
        ? !Number(b.resolved_at || 0) && !Number(b.dismissed_at || 0)
        : !Number(b.read_at || 0);
      if (aActive !== bActive) return aActive ? -1 : 1;
      return Number(b.last_occurred_at || b.created_at || 0)
        - Number(a.last_occurred_at || a.created_at || 0);
    });
}

function _inboxActionLabel(notice) {
  var kind = String(notice.action_kind || '');
  if (kind === 'open_agent') return 'Open agent';
  if (kind === 'open_task') return 'Open task';
  if (kind === 'open_settings') return 'Open settings';
  if (kind === 'retry_board_sync') return 'Retry';
  if (kind === 'open_panel') return 'Open panel';
  return kind === 'open_inbox' ? '' : '';
}

function _inboxRenderNotice(notice) {
  var id = esc(String(notice.id || ''));
  var severity = String(notice.severity || 'info').replace(/[^a-z-]/g, '');
  var unread = !Number(notice.read_at || 0);
  var activeAlert = notice.notice_type === 'alert'
    && !Number(notice.resolved_at || 0)
    && !Number(notice.dismissed_at || 0)
    && !Number(notice.archived_at || 0);
  var archived = Number(notice.archived_at || 0) > 0;
  var actionLabel = _inboxActionLabel(notice);
  var meta = [];
  if (notice.group_name) meta.push(esc(notice.group_name));
  if (notice.category && notice.category !== 'general') {
    meta.push(esc(String(notice.category).replace(/_/g, ' ')));
  }
  meta.push(esc(_inboxTimestamp(notice.last_occurred_at || notice.created_at)));
  if (Number(notice.occurrence_count || 1) > 1) {
    meta.push(esc(String(notice.occurrence_count) + ' occurrences'));
  }

  var html = '<article class="inbox-item inbox-item--' + severity
    + (unread ? ' is-unread' : '')
    + (activeAlert ? ' is-open' : '')
    + (archived ? ' is-archived' : '')
    + '" data-notice-id="' + id + '">';
  html += '<div class="inbox-item-marker" aria-hidden="true"></div>';
  html += '<div class="inbox-item-body">';
  html += '<div class="inbox-item-heading">';
  html += '<strong class="inbox-item-title">' + esc(notice.title || 'Torque') + '</strong>';
  html += '<span class="inbox-item-state">' + esc(_inboxNoticeState(notice)) + '</span>';
  html += '</div>';
  html += '<p class="inbox-item-message">' + esc(notice.message || '') + '</p>';
  html += '<div class="inbox-item-meta">' + meta.join('<span aria-hidden="true">·</span>') + '</div>';
  html += '<div class="inbox-item-actions">';
  if (actionLabel) {
    html += '<button type="button" class="btn-primary btn-sm" onclick="inboxRunAction(\''
      + id + '\')">' + esc(actionLabel) + '</button>';
  }
  if (unread) {
    html += '<button type="button" class="btn-secondary btn-sm" onclick="inboxLifecycle(\''
      + id + '\',\'read\')">Mark read</button>';
  }
  if (notice.notice_type === 'alert' && activeAlert) {
    html += '<button type="button" class="btn-secondary btn-sm" onclick="inboxLifecycle(\''
      + id + '\',\'resolve\')">Resolve</button>';
    html += '<button type="button" class="btn-quiet btn-sm" onclick="inboxLifecycle(\''
      + id + '\',\'dismiss\')">Dismiss</button>';
  }
  html += '<button type="button" class="btn-quiet btn-sm" onclick="inboxLifecycle(\''
    + id + '\',\'' + (archived ? 'restore' : 'archive') + '\')">'
    + (archived ? 'Restore' : 'Archive') + '</button>';
  html += '</div></div></article>';
  return html;
}

function renderInbox() {
  var root = document.getElementById('inbox-popover');
  if (!root) return;
  var oldScroller = root.querySelector('.inbox-list');
  var scrollTop = oldScroller ? oldScroller.scrollTop : 0;
  var summary = _inboxSummary();
  var notices = _inboxSortedNotices();
  var html = '<section class="inbox-panel">';
  html += '<div class="inbox-header">';
  html += '<div><h2>Inbox</h2><p>Durable alerts and activity that need your attention.</p></div>';
  html += '<div class="inbox-header-actions">';
  if (summary.unread_total > 0) {
    html += '<button type="button" class="btn-secondary btn-sm" onclick="inboxMarkAllRead()">Mark all read</button>';
  }
  html += '<button type="button" class="btn-quiet btn-sm inbox-close" '
    + 'onclick="closeInboxPopover({restoreFocus:true})" aria-label="Close notifications">×</button>';
  html += '</div></div>';
  html += '<div class="inbox-toolbar">';
  html += '<div class="ui-segmented-control" role="tablist" aria-label="Inbox view">';
  html += '<button type="button" role="tab" aria-selected="' + (_inboxView === 'alerts')
    + '" class="' + (_inboxView === 'alerts' ? 'active' : '')
    + '" onclick="inboxSetView(\'alerts\')">Alerts'
    + (summary.open_alerts ? '<span class="inbox-tab-count">' + summary.open_alerts + '</span>' : '')
    + '</button>';
  html += '<button type="button" role="tab" aria-selected="' + (_inboxView === 'notifications')
    + '" class="' + (_inboxView === 'notifications' ? 'active' : '')
    + '" onclick="inboxSetView(\'notifications\')">Notifications'
    + (summary.unread_notifications ? '<span class="inbox-tab-count">' + summary.unread_notifications + '</span>' : '')
    + '</button></div>';
  html += '<label class="inbox-archived-toggle"><input type="checkbox" '
    + (_inboxShowArchived ? 'checked ' : '')
    + 'onchange="inboxToggleArchived(this.checked)"> Show archived</label>';
  html += '</div>';
  html += '<div class="inbox-list" role="feed">';
  if (!notices.length) {
    html += '<div class="ui-state ui-state--empty inbox-empty"><strong>'
      + (_inboxView === 'alerts' ? 'No alerts' : 'No notifications')
      + '</strong><span>'
      + (_inboxShowArchived
        ? 'Nothing has been recorded in this view yet.'
        : 'New items will remain here until you acknowledge them.')
      + '</span></div>';
  } else {
    html += notices.map(_inboxRenderNotice).join('');
  }
  if (_inboxHasMore) {
    html += '<div class="inbox-load-more"><button type="button" class="btn-secondary btn-sm" '
      + 'onclick="inboxLoadOlder()">Load older</button></div>';
  }
  html += '</div></section>';
  root.innerHTML = html;
  var scroller = root.querySelector('.inbox-list');
  if (scroller) scroller.scrollTop = scrollTop;
  inboxUpdateBadge();
}

function inboxEnsureLoaded() {
  if (_inboxHistoryRequested || typeof send !== 'function') return;
  _inboxHistoryRequested = true;
  _inboxHistoryOffset = 0;
  send({
    cmd: 'operator_notices_list',
    include_archived: true,
    limit: 200,
    offset: 0,
  });
}

function inboxLoadOlder() {
  if (!_inboxHasMore || typeof send !== 'function') return;
  send({
    cmd: 'operator_notices_list',
    include_archived: true,
    limit: 200,
    offset: _inboxHistoryOffset,
  });
}

function inboxSetView(view) {
  _inboxView = view === 'notifications' ? 'notifications' : 'alerts';
  renderInbox();
}

function inboxToggleArchived(show) {
  _inboxShowArchived = !!show;
  renderInbox();
}

function _inboxLifecycleCommand(action) {
  return {
    read: 'operator_notice_mark_read',
    resolve: 'operator_notice_resolve',
    dismiss: 'operator_notice_dismiss',
    archive: 'operator_notice_archive',
    restore: 'operator_notice_restore',
  }[action] || '';
}

function inboxLifecycle(id, action) {
  var cmd = _inboxLifecycleCommand(action);
  if (!cmd || !id || typeof send !== 'function') return;
  send({ cmd: cmd, id: id });
}

function inboxMarkAllRead() {
  if (typeof send !== 'function') return;
  send({ cmd: 'operator_notices_mark_all_read' });
}

function inboxRunAction(id) {
  var notice = _inboxNoticeMap()[id];
  if (!notice) return;
  if (!Number(notice.read_at || 0)) inboxLifecycle(id, 'read');
  var payload = notice.action_payload && typeof notice.action_payload === 'object'
    ? notice.action_payload
    : {};
  var kind = String(notice.action_kind || '');
  if (kind === 'open_task' && typeof openTaskInBoard === 'function') {
    closeInboxPopover();
    openTaskInBoard(notice.task_id || payload.task_id || payload.task);
  } else if (kind === 'open_agent') {
    closeInboxPopover();
    var agentId = notice.agent_id || payload.agent_id || payload.id;
    if (agentId && typeof onAgentClick === 'function') onAgentClick(agentId);
    if (typeof panelNavOpenPanel === 'function') panelNavOpenPanel('engineer');
  } else if (kind === 'open_panel') {
    if (!payload.panel || payload.panel === 'inbox') return;
    closeInboxPopover();
    if (typeof panelNavOpenPanel === 'function') panelNavOpenPanel(payload.panel);
  } else if (kind === 'open_settings' && typeof openGlobalSettings === 'function') {
    closeInboxPopover();
    openGlobalSettings();
  } else if (kind === 'retry_board_sync' && typeof boardSyncTaskNow === 'function') {
    closeInboxPopover();
    boardSyncTaskNow(notice.task_id || payload.task_id || payload.task, { quiet: true });
  }
}

function inboxReceiveUpsert(op) {
  if (!op || !op.notice || !op.notice.id) return;
  _inboxNoticeMap()[op.notice.id] = op.notice;
  if (op.summary && typeof op.summary === 'object') {
    state.operator_notice_summary = op.summary;
  }
  var acknowledgeFocusedDirectMessage = (op.event === 'publish' || op.event === 'recur')
    && _inboxShouldAcknowledgeFocusedDirectMessage(op.notice);
  if (acknowledgeFocusedDirectMessage) {
    _inboxAcknowledgeFocusedDirectMessage(op.notice);
  }
  inboxUpdateBadge();
  if (_inboxPopoverVisible()) renderInbox();
  if ((op.event === 'publish' || op.event === 'recur')
      && !acknowledgeFocusedDirectMessage
      && typeof _showNoticeToast === 'function') {
    _showNoticeToast(op.notice);
  }
}

function inboxReceiveSummary(op) {
  if (op && op.summary && typeof op.summary === 'object') {
    state.operator_notice_summary = op.summary;
  }
  inboxUpdateBadge();
  if (_inboxPopoverVisible()) renderInbox();
}

function inboxReceiveReadAll(op) {
  var readAt = Number((op && op.read_at) || (Date.now() / 1000));
  var wantedType = String((op && op.notice_type) || '');
  var map = _inboxNoticeMap();
  Object.keys(map).forEach(function(id) {
    var notice = map[id];
    if (!notice || Number(notice.archived_at || 0)) return;
    if (wantedType && notice.notice_type !== wantedType) return;
    if (!Number(notice.read_at || 0)) notice.read_at = readAt;
  });
  inboxReceiveSummary(op || {});
}

function inboxReceiveCommandMessage(msg) {
  if (!msg) return false;
  if (msg.type === 'operator_notices') {
    var offset = Number(msg.offset || 0);
    var next = offset > 0 ? Object.assign({}, _inboxNoticeMap()) : {};
    (msg.notices || []).forEach(function(notice) {
      if (notice && notice.id) next[notice.id] = notice;
    });
    state.operator_notices = next;
    _inboxHistoryRequested = true;
    _inboxHistoryOffset = offset + (msg.notices || []).length;
    _inboxHasMore = !!msg.has_more;
    if (msg.summary) state.operator_notice_summary = msg.summary;
    inboxUpdateBadge();
    if (_inboxPopoverVisible()) renderInbox();
    return true;
  }
  if (msg.type === 'operator_notice' && msg.notice && msg.notice.id) {
    inboxReceiveUpsert({ notice: msg.notice, summary: msg.summary, event: 'response' });
    return true;
  }
  if (msg.type === 'operator_notices_marked_read') {
    inboxReceiveSummary(msg);
    return true;
  }
  return false;
}

function inboxReportClientError(message, opts) {
  opts = opts || {};
  var text = String(message || '').trim();
  if (!text) return false;
  if (typeof send !== 'function') {
    if (typeof _showToast === 'function') _showToast(text, 'error');
    return false;
  }
  send({
    cmd: 'operator_notice_report_client_error',
    title: opts.title || 'Torque error',
    message: text,
    category: opts.category || 'client',
    source: opts.source || 'ui',
    group_name: opts.group_name || '',
    agent_id: opts.agent_id || '',
    task_id: opts.task_id || '',
    action_kind: opts.action_kind || 'open_inbox',
    action_payload: opts.action_payload || {},
    dedupe_key: opts.dedupe_key || '',
  });
  return true;
}

function _inboxInitPopover() {
  if (!document || typeof document.addEventListener !== 'function') return;
  document.addEventListener('click', function(event) {
    if (!_inboxPopoverVisible()) return;
    var root = document.getElementById('inbox-popover');
    var target = event && event.target;
    if (root && target && typeof root.contains === 'function' && root.contains(target)) return;
    if (target && typeof target.closest === 'function'
        && target.closest('.inbox-bell-button')) return;
    closeInboxPopover();
  });
  document.addEventListener('keydown', function(event) {
    if (event && event.key === 'Escape' && _inboxPopoverVisible()) {
      event.preventDefault();
      event.stopPropagation();
      closeInboxPopover({ restoreFocus: true });
    }
  });
  if (typeof window !== 'undefined'
      && window
      && typeof window.addEventListener === 'function') {
    window.addEventListener('resize', function() {
      if (_inboxPopoverVisible()) _positionInboxPopover();
    });
  }
}

_inboxInitPopover();

/* Agent panel — focused-agent router with per-kind renderers */

if (typeof taskIsEngineerMessageFollowup !== 'function') {
  var taskIsEngineerMessageFollowup = function(task) {
    var labels = (task && Array.isArray(task.labels)) ? task.labels : [];
    return labels.indexOf('torque:engineer-message') >= 0;
  };
}

var _agentPanelLastSelectedTabByKind = {};
var _agentPanelCellEventsById = {};
var _agentPanelCellEventsLoadingById = {};
var _agentPanelCellEventsLastFetchById = {};
var _agentPanelCellEventsLastFetchEventAtById = {};
var _AGENT_PANEL_CELL_EVENTS_REFRESH_MS = 1500;
var _AGENT_PANEL_EVENTS_PAGE_SIZE = 20;
var _AGENT_PANEL_EVENTS_SCROLL_THRESHOLD = 80;
var _AGENT_PANEL_VIRTUAL_THRESHOLD = 80;
var _AGENT_PANEL_VIRTUAL_OVERSCAN = 6;
var _AGENT_PANEL_VIRTUAL_DEFAULT_VIEWPORT = 520;
var _AGENT_PANEL_WORKLOG_ROW_HEIGHT = 70;
var _AGENT_PANEL_MESSAGE_ROW_HEIGHT = 104;
var _AGENT_PANEL_DECISION_ROW_HEIGHT = 116;
var _AGENT_PANEL_JOURNAL_ROW_HEIGHT = 96;
var _AGENT_PANEL_JOURNAL_REFRESH_MS = 1500;
var _AGENT_PANEL_JOURNAL_PAGE_SIZE = 50;
var _agentPanelEventsPagerAgentId = '';
var _agentPanelEventsVisibleLimit = _AGENT_PANEL_EVENTS_PAGE_SIZE;
var _agentPanelEventsLastTotal = 0;
var _agentPanelEventsPreRenderAtLiveTail = false;
var _agentPanelEventsInnerTabByAgentId = {};
// Per-(agentId, section) visible-limit pagers for the digest Queued / Sent
// lists. Cell events keeps its own pager above because it also participates in
// scroll-auto-grow; digest sections load-more on explicit click only.
var _agentPanelSectionPagers = {};
var _agentPanelVirtualScrollByKey = {};
var _agentPanelRenderedVirtualMetas = [];
var _agentPanelVirtualRenderFrame = 0;
var _agentPanelWorkerTaskIdCacheByAgent = {};
var _agentPanelDecisionListCacheByArchitect = {};
var _agentPanelDecisionRowsCacheByArchitect = {};
var _agentPanelShowArchivedDecisionsByArchitect = {};
var _agentPanelMessageListCacheByArchitect = {};
var _agentPanelArchitectPeerListByArchitect = {};
var _agentPanelArchitectPeerListRequestedByArchitect = {};
var _agentPanelArchitectPeerComposeDrafts = {};
var _agentPanelArchitectJournalByArchitect = {};
var _agentPanelArchitectJournalLoadingById = {};
var _agentPanelArchitectJournalLastFetchById = {};
var _agentPanelArchitectJournalVisibleLimitById = {};
var _agentPanelArchitectJournalRequestedLimitById = {};
var _agentPanelArchitectJournalInFlightLimitById = {};
var _agentPanelArchitectJournalExhaustedById = {};
var _agentPanelSpecializationsRequestedGroup = '';
var _agentPanelSpecializationsRequestedAt = 0;
var _agentPanelEngineerSpecializationEditors = {};
var _agentPanelMcpCallsByAgent = {};
var _agentPanelMcpCallsLoadingByAgent = {};
var _agentPanelMcpCallsRequestedKeyByAgent = {};
var _agentPanelMcpCallsVisibleLimitByAgent = {};
var _agentPanelMcpCallExpandedByAgent = {};
var _agentPanelMcpFiltersByAgent = {};
var _agentPanelClassManagerByAgent = {};
var _agentPanelClassListByKey = {};
var _agentPanelClassPreviewById = {};
var _agentPanelClassLastRequestedListKey = '';
var _agentPanelClassModalAgentId = '';
var _AGENT_PANEL_MCP_PAGE_SIZE = 50;
var _AGENT_PANEL_MCP_DEFAULT_HOOK = 'PostToolUse';
var _agentPanelTabSpecByKind = {
  architect: [
    { key: 'decisions', label: 'Decisions' },
    { key: 'behavior', label: 'Behavior' },
    { key: 'journal', label: 'Journal' },
    { key: 'messages', label: 'Messages' },
    { key: 'events', label: 'Events' },
  ],
  engineer: [
    { key: 'journal', label: 'Journal' },
    { key: 'behavior', label: 'Behavior' },
    { key: 'events', label: 'Events' },
    { key: 'queued', label: 'Queued' },
    { key: 'worklog', label: 'Completed' },
  ],
  worker: [
    { key: 'events', label: 'Events' },
    { key: 'messages', label: 'Messages' },
    { key: 'worklog', label: 'Worklog' },
  ],
  user: [],
  terminal: [],
};

function _agentPanelEsc(value) {
  if (typeof _esc === 'function') return _esc(value);
  if (typeof esc === 'function') return esc(value);
  return String(value == null ? '' : value);
}

function _agentPanelAttr(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _agentPanelEventAttr(value) {
  return _agentPanelAttr(value);
}

function _agentPanelJsString(value) {
  return JSON.stringify(String(value == null ? '' : value));
}

function _agentPanelNormalizePrState(value, pending) {
  var state = String(value || '').trim().toLowerCase();
  if (!state && pending === true) return 'auto_merge_enabled';
  var aliases = {
    created: 'open',
    failed: 'blocked',
    merge_failed: 'blocked',
    pending: 'auto_merge_enabled',
  };
  return aliases[state] || state;
}

function _agentPanelPrMetaFromSource(source) {
  if (!source || typeof source !== 'object') return {};
  var raw = source.pr;
  if (!raw || typeof raw !== 'object') raw = source.pull_request;
  if (!raw || typeof raw !== 'object') raw = {};
  var pending = null;
  if (Object.prototype.hasOwnProperty.call(raw, 'pending')) pending = !!raw.pending;
  else if (Object.prototype.hasOwnProperty.call(source, 'pr_pending')) pending = !!source.pr_pending;

  var rawState = _agentPanelNormalizePrState(raw.state, pending);
  var statusState = _agentPanelNormalizePrState(raw.status || source.pr_status, pending);
  var state = rawState || statusState || _agentPanelNormalizePrState(source.pr_state, pending);
  if ((statusState === 'auto_merge_enabled' || statusState === 'blocked' || statusState === 'merged')
      && (!rawState || rawState === 'open')) {
    state = statusState;
  }

  var number = raw.number;
  if ((number == null || number === '') && source.pr_number != null && source.pr_number !== '') {
    number = source.pr_number;
  }
  var pr = {
    url: String(raw.url || source.pr_url || '').trim(),
    number: number != null && number !== '' ? number : '',
    state: state,
    merge_state: String(raw.merge_state || source.pr_merge_state || '').trim(),
  };
  return (pr.url || pr.number !== '' || pr.state || pr.merge_state) ? pr : {};
}

function _agentPanelPrStateLabel(pr) {
  var state = _agentPanelNormalizePrState(pr && pr.state, pr && pr.pending);
  var labels = {
    auto_merge_enabled: 'Auto-merge pending',
    open: 'PR open',
    blocked: 'PR blocked',
    merged: 'PR merged',
    closed: 'PR closed',
    draft: 'PR draft',
  };
  if (labels[state]) return labels[state];
  if (!state && pr && (pr.url || pr.number !== '')) return 'PR open';
  if (!state) return '';
  return 'PR ' + state.replace(/[_-]+/g, ' ');
}

function _agentPanelPrStateClass(pr) {
  var state = _agentPanelNormalizePrState(pr && pr.state, pr && pr.pending);
  if (state === 'auto_merge_enabled') return 'pending';
  if (state === 'merged') return 'merged';
  if (state === 'blocked' || state === 'closed') return 'blocked';
  if (state === 'open' || state === 'draft') return 'open';
  return 'unknown';
}

function _agentPanelRenderPrValue(pr) {
  if (!pr || typeof pr !== 'object') return '';
  if (!pr.url && pr.number === '' && !pr.state && !pr.merge_state) return '';
  var label = pr.number !== '' && pr.number != null ? ('#' + pr.number) : 'Pull request';
  var stateLabel = _agentPanelPrStateLabel(pr);
  var html = '<span class="agent-panel-pr-inline">';
  if (pr.url) {
    html += '<a class="agent-panel-pr-link" href="' + _agentPanelEsc(pr.url)
      + '" target="_blank" rel="noopener noreferrer"'
      + ' onclick="event.stopPropagation()" title="' + _agentPanelEsc(pr.url) + '">'
      + _agentPanelEsc(label) + '</a>';
  } else {
    html += '<span class="agent-panel-pr-link-muted">' + _agentPanelEsc(label) + '</span>';
  }
  if (stateLabel) {
    html += '<span class="agent-panel-pr-state agent-panel-pr-state-'
      + _agentPanelEsc(_agentPanelPrStateClass(pr)) + '">'
      + _agentPanelEsc(stateLabel) + '</span>';
  }
  html += '</span>';
  return html;
}

function _agentPanelDomIdToken(value) {
  return String(value == null ? '' : value)
    .replace(/[^a-zA-Z0-9_-]/g, '-')
    .replace(/^-+|-+$/g, '') || 'agent';
}

function _agentPanelKind(agent) {
  if (!agent) return '';
  if ((agent.cell_type || '') === 'terminal') return 'terminal';
  var kind = String(agent.kind || '').trim();
  if (kind === 'architect' || kind === 'engineer' || kind === 'worker' || kind === 'user') return kind;
  return 'worker';
}

function _agentPanelKindBadge(kind) {
  var label = String(kind || '').trim();
  var cls = 'engineer-row-kind';
  if (label === 'architect' || label === 'engineer' || label === 'worker') {
    cls += ' engineer-row-kind-' + label;
  }
  return '<span class="' + cls + '">' + _agentPanelEsc(label) + '</span>';
}

function _agentPanelAgentDisplayName(agent, fallback) {
  return String((agent && (agent.name || agent.slug || agent.id)) || fallback || 'Unknown');
}

function _agentPanelVirtualUserPrincipal(group) {
  group = String(group || '').trim();
  return {
    id: 'principal:' + group + ':user',
    name: 'User',
    slug: 'user',
    kind: 'user',
    group: group,
    cell_type: 'principal',
  };
}

function _agentPanelAgentVisibleInCurrentMode(agent) {
  if (!agent) return null;
  if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(agent)) return null;
  if (typeof _singleGroupModeEnabled !== 'function'
      || !_singleGroupModeEnabled()
      || typeof _activeGroup !== 'function') {
    return agent;
  }
  var activeGroup = _activeGroup() || '';
  if (!activeGroup) return agent;
  return String(agent.group || '') === activeGroup ? agent : null;
}

function _resolveFocusedAgent() {
  if (typeof focusedItemId === 'undefined' || !focusedItemId) return null;
  if (!state || !state.agents) return null;
  if (state.agents[focusedItemId]) {
    return _agentPanelAgentVisibleInCurrentMode(state.agents[focusedItemId]);
  }
  // Legacy principal focus ids (`principal:<group>:<architect-id|user>`)
  // are persisted by older sessions, but no longer appear in the grid nav
  // model. Resolve them so stale focus still opens a useful panel.
  var meta = (typeof _navMeta === 'function') ? _navMeta(focusedItemId) : null;
  if (meta && meta.type === 'principal') {
    var pid = String(meta.principalId || '');
    if (pid && state.agents[pid]) {
      return _agentPanelAgentVisibleInCurrentMode(state.agents[pid]);
    }
    if (pid) return null;
    return _agentPanelAgentVisibleInCurrentMode(
      _agentPanelVirtualUserPrincipal(meta.group || '')
    );
  }
  if (typeof focusedItemId === 'string' && focusedItemId.indexOf('principal:') === 0) {
    var lastColon = focusedItemId.lastIndexOf(':');
    if (lastColon > 'principal:'.length - 1) {
      var tail = focusedItemId.slice(lastColon + 1);
      if (tail && tail !== 'user' && state.agents[tail]) {
        return _agentPanelAgentVisibleInCurrentMode(state.agents[tail]);
      }
      if (tail === 'user') {
        var group = focusedItemId.slice('principal:'.length, lastColon);
        return _agentPanelAgentVisibleInCurrentMode(
          _agentPanelVirtualUserPrincipal(group)
        );
      }
    }
  }
  if (typeof selectedAgentId !== 'undefined'
      && selectedAgentId
      && state.agents[selectedAgentId]) {
    return _agentPanelAgentVisibleInCurrentMode(state.agents[selectedAgentId]);
  }
  return null;
}

function _agentPanelSelectedTab(kind) {
  kind = String(kind || '').trim();
  if (!kind) return '';
  return _agentPanelLastSelectedTabByKind[kind] || '';
}

function _agentPanelTabSpec(kind) {
  return _agentPanelTabSpecByKind[String(kind || '').trim()] || [];
}

function _agentPanelDefaultTab(kind) {
  var tabs = _agentPanelTabSpec(kind);
  return tabs.length ? tabs[0].key : '';
}

function _agentPanelActiveTab(kind) {
  var selected = _agentPanelSelectedTab(kind);
  var tabs = _agentPanelTabSpec(kind);
  for (var i = 0; i < tabs.length; i++) {
    if (tabs[i].key === selected) return selected;
  }
  return _agentPanelDefaultTab(kind);
}

function agentPanelSelectTab(tab) {
  var agent = _resolveFocusedAgent();
  if (!agent) return;
  var kind = _agentPanelKind(agent);
  if (!kind) return;
  tab = String(tab || '');
  if (tab === 'mcp' && _agentPanelCanUseEventInnerTabs(agent)) {
    var agentId = String(agent.id || '');
    if (agentId) _agentPanelEventsInnerTabByAgentId[agentId] = 'mcp';
    tab = 'events';
  }
  var previousTab = _agentPanelActiveTab(kind);
  _agentPanelLastSelectedTabByKind[kind] = tab;
  var activeTab = _agentPanelActiveTab(kind);
  if (_agentPanelRenderFocusedTabInPlace(agent, kind, previousTab, activeTab)) return;
  renderAgentPanel();
}

function _agentPanelEventsInnerTab(agent) {
  var agentId = String((agent && agent.id) || '');
  var selected = agentId ? _agentPanelEventsInnerTabByAgentId[agentId] : '';
  if (selected === 'lifecycle' || selected === 'mcp') return selected;
  return 'inbox';
}

function _agentPanelCanUseEventInnerTabs(agent) {
  var kind = _agentPanelKind(agent);
  return kind === 'engineer' || kind === 'architect' || kind === 'worker';
}

function _agentPanelIsMcpSubtabActive(agent) {
  if (!agent || !_agentPanelCanUseEventInnerTabs(agent)) return false;
  return _agentPanelActiveTab(_agentPanelKind(agent)) === 'events'
    && _agentPanelEventsInnerTab(agent) === 'mcp';
}

function agentPanelSelectEventsInnerTab(tab) {
  var agent = _resolveFocusedAgent();
  if (!agent || !_agentPanelCanUseEventInnerTabs(agent)) return;
  if (_agentPanelActiveTab(_agentPanelKind(agent)) !== 'events') return;
  var agentId = String(agent.id || '');
  if (!agentId) return;
  tab = String(tab || '');
  var next = (tab === 'lifecycle' || tab === 'mcp') ? tab : 'inbox';
  if (_agentPanelEventsInnerTab(agent) === next) return;
  _agentPanelEventsInnerTabByAgentId[agentId] = next;
  renderAgentPanel();
}

function _agentPanelTimeAgo(ts) {
  if (typeof _relativeTime === 'function') return _relativeTime(ts);
  if (typeof _engineerTimeAgo === 'function') return _engineerTimeAgo(ts);
  ts = Number(ts || 0);
  if (!ts) return '';
  var diff = (Date.now() / 1000) - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function _agentPanelTimestamp(ts) {
  ts = Number(ts || 0);
  if (!ts) return '';
  var relative = _agentPanelTimeAgo(ts);
  var exact = '';
  try {
    exact = new Date(ts * 1000).toISOString()
      .replace('T', ' ')
      .replace(/\.\d{3}Z$/, ' UTC');
  } catch (err) {
    exact = '';
  }
  if (relative && exact) return relative + ' · ' + exact;
  return relative || exact;
}

function _agentPanelMessageKey(message, index) {
  message = message || {};
  var id = String(message.id || '').trim();
  if (id) return 'message-' + id;
  var threadId = String(message.thread_id || '').trim();
  var action = String(message.action || '').trim();
  var ts = Number(message.timestamp || 0);
  if (threadId && ts) return 'message-' + threadId + '-' + ts + '-' + action;
  if (ts) return 'message-' + ts + '-' + action;
  return 'message-' + index + '-' + action;
}

function _agentPanelMessageKindLabel(kind) {
  kind = String(kind || '').trim().toLowerCase();
  if (!kind) return 'User';
  if (kind === 'architect') return 'Architect';
  if (kind === 'engineer') return 'Engineer';
  if (kind === 'worker') return 'Worker';
  if (kind === 'user' || kind === 'human') return 'User';
  if (kind === 'engineer' || kind === 'system' || kind === 'torque') return 'User';
  return kind.charAt(0).toUpperCase() + kind.slice(1).replace(/_/g, ' ');
}

function _agentPanelAgentForId(agentId) {
  agentId = String(agentId || '').trim();
  if (!agentId || !state || !state.agents) return null;
  return state.agents[agentId] || null;
}

function _agentPanelMessagePeerId(agent, message, direction) {
  message = message || {};
  var agentId = String((agent && agent.id) || '').trim();
  var peerId = String(message.peer_id || '').trim();
  if (peerId) return peerId;
  var senderId = String(message.sender_id || message.sender_agent_id || '').trim();
  var recipientId = String(message.recipient_id || message.recipient_agent_id || '').trim();
  if (direction === 'out' && recipientId && recipientId !== agentId) return recipientId;
  if (direction === 'in' && senderId && senderId !== agentId) return senderId;
  if (senderId && senderId !== agentId) return senderId;
  if (recipientId && recipientId !== agentId) return recipientId;
  return '';
}

function _agentPanelMessagePeerKind(agent, message, direction, senderKind) {
  message = message || {};
  var peer = _agentPanelAgentForId(_agentPanelMessagePeerId(agent, message, direction));
  if (peer && peer.kind) return peer.kind;
  if (message.peer_kind) return String(message.peer_kind || '').trim();
  if (direction === 'out' && message.recipient_kind) return String(message.recipient_kind || '').trim();
  if (direction === 'in' && message.sender_kind) return String(message.sender_kind || '').trim();
  var action = String(message.action || '').trim();
  if (action === 'architect_peer_message' || action === 'architect_peer_reply') return 'architect';
  if (action === 'architect_message' || action === 'architect_reply') return 'engineer';
  if (action === 'engineer_message_architect' || action === 'engineer_reply') return 'engineer';
  return direction === 'in' ? String(senderKind || '').trim() : '';
}

function _agentPanelMessageAttributionHtml(agent, message, direction, senderKind) {
  message = message || {};
  var peerId = _agentPanelMessagePeerId(agent, message, direction);
  var explicitName = String(
    (direction === 'out'
      ? (message.recipient_name || message.peer_name)
      : (message.sender_name || message.peer_name)) || ''
  ).trim();
  var peer = _agentPanelAgentForId(peerId);
  var peerKind = _agentPanelMessagePeerKind(agent, message, direction, senderKind);
  var name = peer
    ? _agentPanelAgentDisplayName(peer, peerId)
    : (explicitName || peerId || (peerKind ? _agentPanelMessageKindLabel(peerKind) : ''));
  if (!name) return '';
  var label = direction === 'out' ? 'To' : 'From';
  var cls = 'agent-panel-message-attribution agent-panel-message-attribution-' + direction;
  return '<span class="' + cls + '">'
    + '<span class="agent-panel-message-attribution-label">' + label + ':</span>'
    + '<span class="agent-panel-message-attribution-name">' + _agentPanelEsc(name) + '</span>'
    + '</span>';
}

function _agentPanelMessageDirection(agent, message) {
  message = message || {};
  var raw = String(message.direction || '').trim().toLowerCase();
  if (raw === 'sent' || raw === 'out' || raw === 'outgoing') return 'out';
  if (raw === 'received' || raw === 'in' || raw === 'incoming') return 'in';

  var agentId = String((agent && agent.id) || '');
  var senderId = String(message.sender_id || '').trim();
  if (senderId && agentId) return senderId === agentId ? 'out' : 'in';

  var action = String(message.action || '').trim();
  var kind = _agentPanelKind(agent);
  if (action === 'architect_message' || action === 'architect_reply') {
    return kind === 'architect' ? 'out' : 'in';
  }
  if (action === 'engineer_message_architect' || action === 'engineer_reply') {
    return kind === 'engineer' ? 'out' : 'in';
  }
  if (action === 'engineer_message' || action === 'system') return 'in';
  return 'out';
}

function _agentPanelMessageSenderKind(agent, message, direction) {
  message = message || {};
  var explicitKind = String(message.sender_kind || '').trim();
  if (explicitKind) return explicitKind;

  var sender = _agentPanelAgentForId(message.sender_id);
  if (sender && sender.kind) return sender.kind;

  if (direction === 'out') {
    return _agentPanelKind(agent) || 'user';
  }
  if (direction === 'in') {
    if (message.peer_kind) return String(message.peer_kind || '').trim();
    var peer = _agentPanelAgentForId(message.peer_id);
    if (peer && peer.kind) return peer.kind;
  }

  var action = String(message.action || '').trim();
  if (action.indexOf('architect_') === 0) return 'architect';
  if (action.indexOf('engineer_') === 0) return 'engineer';
  if (action === 'engineer_message' || action === 'system') return 'user';
  return _agentPanelKind(agent) || 'user';
}

function _agentPanelMessageActionLabel(action) {
  action = String(action || '').trim();
  if (!action) return 'message';
  if (action === 'engineer_peer_notify') return 'engineer peer notify';
  if (action === 'engineer_peer_reply') return 'engineer peer reply';
  return action.replace(/_/g, ' ');
}

function _agentPanelMessageIsPeer(message) {
  var action = String((message && message.action) || '').trim();
  return action === 'architect_peer_message' || action === 'architect_peer_reply';
}

function _agentPanelMessageContext(message) {
  message = message || {};
  var context = (message.context && typeof message.context === 'object')
    ? message.context
    : {};
  return {
    task_ids: Array.isArray(message.context_task_ids)
      ? message.context_task_ids
      : (Array.isArray(context.task_ids) ? context.task_ids : []),
    engineer_ids: Array.isArray(message.context_engineer_ids)
      ? message.context_engineer_ids
      : (Array.isArray(context.engineer_ids) ? context.engineer_ids : []),
    decision_ids: Array.isArray(message.context_decision_ids)
      ? message.context_decision_ids
      : (Array.isArray(context.decision_ids) ? context.decision_ids : []),
    summary: String(
      message.context_summary != null ? message.context_summary : (context.summary || '')
    ),
  };
}

function _agentPanelFirstLine(value, limit) {
  var text = String(value || '').trim().split(/\r?\n/)[0] || '';
  limit = Math.max(8, Number(limit || 56) || 56);
  if (text.length <= limit) return text;
  return text.slice(0, limit - 1).trimEnd() + '…';
}

function _agentPanelTaskLabel(taskId) {
  var id = String(taskId || '').trim();
  var task = id && state && state.board_tasks ? state.board_tasks[id] : null;
  if (!task) return id;
  return id + ' · ' + _agentPanelFirstLine(task.task || task.title || '', 46);
}

function _agentPanelAgentLabel(agentId) {
  var id = String(agentId || '').trim();
  var agent = _agentPanelAgentForId(id);
  if (!agent) return id;
  return _agentPanelAgentDisplayName(agent, id);
}

function _agentPanelDecisionForId(decisionId) {
  var id = String(decisionId || '').trim();
  if (!id) return null;
  var stores = [];
  if (state && state.decisions) stores.push(state.decisions);
  if (state && state.architect_decisions && state.architect_decisions !== state.decisions) {
    stores.push(state.architect_decisions);
  }
  for (var i = 0; i < stores.length; i++) {
    var store = stores[i] || {};
    if (store[id]) return store[id];
  }
  return null;
}

function _agentPanelDecisionLabel(decisionId) {
  var id = String(decisionId || '').trim();
  var decision = _agentPanelDecisionForId(id);
  if (!decision) return id;
  return id + ' · ' + _agentPanelFirstLine(decision.title || 'Decision', 46);
}

function _agentPanelRenderContextChips(kind, values, labelFn) {
  values = Array.isArray(values) ? values.filter(function(value) {
    return String(value || '').trim();
  }) : [];
  if (!values.length) return '';
  var limit = 3;
  var html = '';
  for (var i = 0; i < Math.min(limit, values.length); i++) {
    html += '<span class="agent-panel-message-context-chip agent-panel-message-context-'
      + _agentPanelAttr(kind) + '">'
      + '<span class="agent-panel-message-context-kind">'
      + _agentPanelEsc(kind) + '</span>'
      + _agentPanelEsc(labelFn ? labelFn(values[i]) : values[i])
      + '</span>';
  }
  if (values.length > limit) {
    html += '<span class="agent-panel-message-context-more">+'
      + (values.length - limit) + ' more</span>';
  }
  return html;
}

function _agentPanelPeerNameGroup(agent, message) {
  message = message || {};
  var direction = _agentPanelMessageDirection(agent, message);
  var peerId = String(message.peer_id || '').trim();
  if (!peerId) {
    peerId = direction === 'out'
      ? String(message.recipient_id || '').trim()
      : String(message.sender_id || '').trim();
  }
  var peer = _agentPanelAgentForId(peerId);
  var explicitName = String(
    (direction === 'out'
      ? (message.recipient_name || message.peer_name)
      : (message.sender_name || message.peer_name)) || ''
  ).trim();
  var name = peer ? _agentPanelAgentDisplayName(peer, peerId) : (explicitName || peerId);
  var group = String(
    (peer && peer.group) || message.group || (agent && agent.group) || ''
  ).trim();
  return {
    id: peerId,
    name: name || 'Peer',
    group: group,
  };
}

function _agentPanelMessagePeerAffordances(agent, message) {
  if (!_agentPanelMessageIsPeer(message)) return '';
  var peer = _agentPanelPeerNameGroup(agent, message);
  var html = '<span class="agent-panel-message-peer" title="Peer Architect">'
    + _agentPanelEsc(peer.name)
    + (peer.group ? ' · ' + _agentPanelEsc(peer.group) : '')
    + '</span>';
  if (message && message.ack_required) {
    html += '<span class="agent-panel-message-ack">Ack required</span>';
  }
  return html;
}

function _agentPanelMessageContextPreview(message) {
  if (!_agentPanelMessageIsPeer(message)) return '';
  var context = _agentPanelMessageContext(message);
  var chips = '';
  chips += _agentPanelRenderContextChips('task', context.task_ids, _agentPanelTaskLabel);
  chips += _agentPanelRenderContextChips('engineer', context.engineer_ids, _agentPanelAgentLabel);
  chips += _agentPanelRenderContextChips('decision', context.decision_ids, _agentPanelDecisionLabel);
  var summary = String(context.summary || '').trim();
  if (!chips && !summary) return '';
  var html = '<div class="agent-panel-message-context-preview">';
  if (summary) {
    html += '<div class="agent-panel-message-context-summary">'
      + _agentPanelEsc(summary) + '</div>';
  }
  if (chips) {
    html += '<div class="agent-panel-message-context-chips">' + chips + '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelMessageCardHtml(agent, message, index, options) {
  message = message || {};
  options = options || {};
  var action = String(message.action || 'progress');
  var direction = String(options.direction || '').trim();
  if (direction !== 'in' && direction !== 'out') {
    direction = _agentPanelMessageDirection(agent, message);
  }
  if (direction !== 'in' && direction !== 'out') direction = 'in';
  var senderKind = String(options.senderKind || '').trim()
    || _agentPanelMessageSenderKind(agent, message, direction);
  var body = options.body != null
    ? String(options.body)
    : String(message.message || action || '');
  var anchorKey = options.anchorKey != null
    ? String(options.anchorKey || '')
    : _agentPanelMessageKey(message, index);
  var anchorAttr = String(options.anchorAttr || 'data-agent-panel-anchor')
    .replace(/[^a-zA-Z0-9_:-]/g, '');
  if (!anchorAttr) anchorAttr = 'data-agent-panel-anchor';
  var extraAttrs = String(options.extraAttrs || '');
  var rowHtml = '<div class="agent-panel-message-card agent-panel-message-' + _agentPanelAttr(direction)
    + '" ' + anchorAttr + '="' + _agentPanelAttr(anchorKey) + '"'
    + (extraAttrs ? ' ' + extraAttrs : '') + '>';
  rowHtml += '<div class="agent-panel-message-card-header">';
  rowHtml += '<div class="agent-panel-message-meta">';
  if (options.attributionHtml !== undefined) {
    rowHtml += String(options.attributionHtml || '');
  } else {
    rowHtml += _agentPanelMessageAttributionHtml(agent, message, direction, senderKind);
  }
  rowHtml += '<span class="agent-panel-message-sender">'
    + _agentPanelEsc(_agentPanelMessageKindLabel(senderKind)) + '</span>';
  if (options.showDirection !== false) {
    rowHtml += '<span class="agent-panel-message-direction">'
      + _agentPanelEsc(direction === 'in' ? 'In' : 'Out') + '</span>';
  }
  rowHtml += '<span class="agent-panel-message-action">'
    + _agentPanelEsc(_agentPanelMessageActionLabel(action)) + '</span>';
  if (options.peerAffordancesHtml !== undefined) {
    rowHtml += String(options.peerAffordancesHtml || '');
  } else if (options.showPeerAffordances !== false) {
    rowHtml += _agentPanelMessagePeerAffordances(agent, message);
  }
  if (options.metaHtml) rowHtml += String(options.metaHtml || '');
  rowHtml += '</div>';
  var timeLabel = options.timeLabel !== undefined
    ? String(options.timeLabel || '')
    : _agentPanelTimestamp(message.timestamp);
  rowHtml += '<span class="agent-panel-message-time">'
    + _agentPanelEsc(timeLabel) + '</span>';
  rowHtml += '</div>';
  rowHtml += '<div class="agent-panel-message-body">' + _agentPanelEsc(body) + '</div>';
  if (options.contextHtml !== undefined) {
    rowHtml += String(options.contextHtml || '');
  } else {
    rowHtml += _agentPanelMessageContextPreview(message);
  }
  rowHtml += '</div>';
  return rowHtml;
}

function _agentPanelAnchorItems(container) {
  if (!container || typeof container.querySelectorAll !== 'function') return [];
  var results = [];
  var seen = [];
  var selectors = ['[data-agent-panel-anchor]', '[data-engineer-anchor]'];
  for (var i = 0; i < selectors.length; i++) {
    var items = container.querySelectorAll(selectors[i]) || [];
    for (var j = 0; j < items.length; j++) {
      var item = items[j];
      if (!item) continue;
      if (seen.indexOf(item) >= 0) continue;
      seen.push(item);
      results.push(item);
    }
  }
  return results;
}

function _agentPanelAnchorKey(item) {
  if (!item) return '';
  if (item.dataset) {
    if (item.dataset.agentPanelAnchor) return String(item.dataset.agentPanelAnchor);
    if (item.dataset.engineerAnchor) return String(item.dataset.engineerAnchor);
  }
  if (typeof item.getAttribute === 'function') {
    var key = item.getAttribute('data-agent-panel-anchor');
    if (key) return String(key);
    key = item.getAttribute('data-engineer-anchor');
    if (key) return String(key);
  }
  return '';
}

function _agentPanelScrollContainer(root) {
  if (!root || typeof root.querySelector !== 'function') return null;
  return root.querySelector('.agent-panel-message-list')
    || root.querySelector('.agent-panel-content');
}

function _agentPanelVirtualKey(parts) {
  var values = Array.isArray(parts) ? parts : [];
  return values.map(function(part) {
    return String(part == null ? '' : part).replace(/\|/g, '%7C');
  }).join('|');
}

function _agentPanelFocusedSurfaceKey(agent, tab, surface) {
  return _agentPanelVirtualKey([
    'agent',
    (agent && agent.id) || '',
    tab || '',
    surface || '',
  ]);
}

function _agentPanelLegacyWorklogVirtualKey(group, restricted) {
  return _agentPanelVirtualKey([
    'legacy-worklog',
    group || '',
    restricted ? 'owned' : 'all',
  ]);
}

function _agentPanelVirtualMetasForSurface(agent, activeTab) {
  if (!agent) return [];
  var kind = _agentPanelKind(agent);
  if (kind === 'worker' && activeTab === 'worklog') {
    return [{
      key: _agentPanelFocusedSurfaceKey(agent, activeTab, 'worker-tasks'),
      scrollSelector: '.agent-panel-content',
    }];
  }
  if (kind === 'worker' && activeTab === 'messages') {
    return [{
      key: _agentPanelFocusedSurfaceKey(agent, activeTab, 'messages'),
      scrollSelector: '.agent-panel-message-list',
    }];
  }
  if (kind === 'engineer' && activeTab === 'worklog') {
    // Engineer worklog now uses the "last 20 + Load older" section pager
    // instead of windowed virtualization, so there is no virtual-scroll
    // record to capture or restore. Scroll position survives rerenders
    // through the shared anchor-restore helper.
    return [];
  }
  if (kind === 'architect' && activeTab === 'decisions') {
    return [{
      key: _agentPanelFocusedSurfaceKey(agent, activeTab, 'decisions'),
      scrollSelector: '.agent-panel-content',
    }];
  }
  if (kind === 'architect' && activeTab === 'messages') {
    return [{
      key: _agentPanelFocusedSurfaceKey(agent, activeTab, 'messages'),
      scrollSelector: '.agent-panel-message-list',
    }];
  }
  if (kind === 'architect' && activeTab === 'journal') {
    // The architect journal is paged with concrete DOM rows instead of the
    // fixed-row-height virtual list. Journal entries can be arbitrarily tall;
    // virtual scroll's estimated max height could clamp scrollTop and snap
    // the operator away from older entries during frequent journal refreshes.
    return [];
  }
  return [];
}

function _agentPanelRecordVirtualScroll(key, container) {
  if (!key || !container) return;
  var rec = _agentPanelVirtualScrollByKey[key] || {};
  if (typeof container.scrollTop === 'number') rec.top = Math.max(0, container.scrollTop);
  if (typeof container.clientHeight === 'number' && container.clientHeight > 0) {
    rec.viewportHeight = container.clientHeight;
  }
  _agentPanelVirtualScrollByKey[key] = rec;
}

function _agentPanelCaptureVirtualScrolls(root, metas) {
  if (!root || typeof root.querySelector !== 'function') return;
  metas = metas || [];
  for (var i = 0; i < metas.length; i++) {
    var meta = metas[i] || {};
    var container = root.querySelector(meta.scrollSelector || '.agent-panel-content');
    _agentPanelRecordVirtualScroll(meta.key, container);
  }
}

function _agentPanelVirtualScrollTop(key) {
  var rec = _agentPanelVirtualScrollByKey[key] || {};
  return Math.max(0, Number(rec.top || 0));
}

function _agentPanelVirtualViewportHeight(key) {
  var rec = _agentPanelVirtualScrollByKey[key] || {};
  return Math.max(
    120,
    Number(rec.viewportHeight || 0) || _AGENT_PANEL_VIRTUAL_DEFAULT_VIEWPORT
  );
}

function _agentPanelVirtualRange(key, total, rowHeight, overscan) {
  total = Math.max(0, Number(total) || 0);
  rowHeight = Math.max(1, Number(rowHeight) || _AGENT_PANEL_WORKLOG_ROW_HEIGHT);
  overscan = Math.max(0, Number(overscan) || _AGENT_PANEL_VIRTUAL_OVERSCAN);
  if (total <= _AGENT_PANEL_VIRTUAL_THRESHOLD) {
    return {
      start: 0,
      end: total,
      before: 0,
      after: 0,
      virtualized: false,
    };
  }
  var viewport = _agentPanelVirtualViewportHeight(key);
  var rawScrollTop = _agentPanelVirtualScrollTop(key);
  var maxScrollTop = Math.max(0, (total * rowHeight) - viewport);
  var scrollTop = Math.min(rawScrollTop, maxScrollTop);
  if (scrollTop !== rawScrollTop && key) {
    var rec = _agentPanelVirtualScrollByKey[key] || {};
    rec.top = scrollTop;
    _agentPanelVirtualScrollByKey[key] = rec;
  }
  var visible = Math.ceil(viewport / rowHeight) + (overscan * 2);
  var start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  start = Math.min(start, Math.max(0, total - Math.max(1, visible)));
  var end = Math.min(total, start + Math.max(1, visible));
  return {
    start: start,
    end: end,
    before: start * rowHeight,
    after: Math.max(0, (total - end) * rowHeight),
    virtualized: true,
  };
}

function _agentPanelVirtualSpacer(height, className) {
  height = Math.max(0, Math.round(Number(height) || 0));
  if (!height) return '';
  return '<div class="' + _agentPanelEsc(className || 'agent-panel-virtual-spacer')
    + '" aria-hidden="true" style="height:' + height + 'px"></div>';
}

function _agentPanelRegisterVirtualMeta(meta) {
  if (!meta || !meta.key) return;
  _agentPanelRenderedVirtualMetas.push(meta);
}

function _agentPanelRenderVirtualList(opts) {
  opts = opts || {};
  var key = String(opts.key || '');
  var total = Math.max(0, Number(opts.total) || 0);
  var rowHeight = Number(opts.rowHeight) || _AGENT_PANEL_WORKLOG_ROW_HEIGHT;
  var range = _agentPanelVirtualRange(key, total, rowHeight, opts.overscan);
  var listClass = opts.listClass || 'agent-panel-worklog-list';
  var attrName = opts.anchorAttribute || 'data-agent-panel-virtual-key';
  var html = '<div class="' + _agentPanelEsc(listClass) + '" '
    + attrName + '="' + _agentPanelEsc(key) + '"'
    + ' data-agent-panel-virtualized="' + (range.virtualized ? 'true' : 'false') + '">';
  html += _agentPanelVirtualSpacer(range.before, opts.spacerClass);
  for (var i = range.start; i < range.end; i++) {
    html += opts.renderItem ? opts.renderItem(i) : '';
  }
  html += _agentPanelVirtualSpacer(range.after, opts.spacerClass);
  html += '</div>';
  _agentPanelRegisterVirtualMeta({
    key: key,
    total: total,
    rowHeight: rowHeight,
    scrollSelector: opts.scrollSelector || '.agent-panel-content',
  });
  return html;
}

function _agentPanelScheduleVirtualRender() {
  if (_agentPanelVirtualRenderFrame) return;
  var scheduler = typeof requestAnimationFrame === 'function'
    ? requestAnimationFrame
    : function(fn) { return setTimeout(fn, 0); };
  _agentPanelVirtualRenderFrame = scheduler(function() {
    _agentPanelVirtualRenderFrame = 0;
    if (typeof _agentPanelRefreshCurrentTab === 'function'
        && _agentPanelRefreshCurrentTab()) {
      return;
    }
    if (typeof renderAgentPanel === 'function') renderAgentPanel();
  });
}

function _agentPanelVirtualScrollHandler(meta, evt) {
  var container = (evt && (evt.currentTarget || evt.target)) || this;
  if (!meta || !meta.key || !container) return;
  _agentPanelRecordVirtualScroll(meta.key, container);
  if (Number(meta.total || 0) <= _AGENT_PANEL_VIRTUAL_THRESHOLD) return;
  _agentPanelScheduleVirtualRender();
}

function _agentPanelAttachVirtualScrolls(root) {
  if (!root || typeof root.querySelector !== 'function') return;
  var touchedContainers = [];
  for (var i = 0; i < _agentPanelRenderedVirtualMetas.length; i++) {
    var meta = _agentPanelRenderedVirtualMetas[i] || {};
    var container = root.querySelector(meta.scrollSelector || '.agent-panel-content');
    if (!container || typeof container.addEventListener !== 'function') continue;
    if (touchedContainers.indexOf(container) < 0) {
      _agentPanelDetachVirtualScrolls(container);
      touchedContainers.push(container);
    }
    if (typeof container.scrollTop === 'number') {
      _agentPanelRecordVirtualScroll(meta.key, container);
    }
    var handler = function(boundMeta) {
      return function(evt) {
        _agentPanelVirtualScrollHandler(boundMeta, evt);
      };
    }(meta);
    if (!container._agentPanelVirtualScrollHandlers) {
      container._agentPanelVirtualScrollHandlers = {};
    }
    container._agentPanelVirtualScrollHandlers[meta.key] = handler;
    container.addEventListener('scroll', handler);
  }
}

function _agentPanelDetachVirtualScrolls(container) {
  if (!container || typeof container.removeEventListener !== 'function') return;
  var handlers = container._agentPanelVirtualScrollHandlers || {};
  for (var key in handlers) {
    if (handlers[key]) container.removeEventListener('scroll', handlers[key]);
  }
  container._agentPanelVirtualScrollHandlers = {};
}

function _agentPanelDetachVirtualScrollsForRoot(root) {
  if (!root || typeof root.querySelector !== 'function') return;
  var selectors = ['.agent-panel-content', '.agent-panel-message-list'];
  var seen = [];
  for (var i = 0; i < selectors.length; i++) {
    var container = root.querySelector(selectors[i]);
    if (!container || seen.indexOf(container) >= 0) continue;
    seen.push(container);
    _agentPanelDetachVirtualScrolls(container);
  }
}

function _agentPanelRestoreVirtualScrolls(root, metas) {
  if (!root || typeof root.querySelector !== 'function') return;
  metas = metas || [];
  for (var i = 0; i < metas.length; i++) {
    var meta = metas[i] || {};
    var key = meta.key || '';
    if (!key) continue;
    var rec = _agentPanelVirtualScrollByKey[key] || {};
    var container = root.querySelector(meta.scrollSelector || '.agent-panel-content');
    if (!container || typeof container.scrollTop !== 'number') continue;
    container.scrollTop = Math.max(0, Number(rec.top || 0));
  }
}

function _agentPanelCaptureScrollAnchor(container) {
  if (!container || typeof container.getBoundingClientRect !== 'function') return null;
  var items = _agentPanelAnchorItems(container);
  if (!items.length) return null;
  var containerRect = container.getBoundingClientRect();
  // Pick the first item that overlaps the viewport. Items entirely above or
  // entirely below the container are rejected so we never anchor to something
  // the user can't see — a virtualized list can leave the rendered window
  // outside the viewport (e.g. wheel-scrolled into a before/after spacer with
  // a stale virtual record), and falling back to such an item produces a
  // bogus capture offset that drives scrollTop toward 0 on the next rerender.
  var best = null;
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    if (!item || typeof item.getBoundingClientRect !== 'function') continue;
    var rect = item.getBoundingClientRect();
    if (rect.bottom >= containerRect.top && rect.top <= containerRect.bottom) {
      best = item;
      break;
    }
  }
  if (!best) return null;
  var anchorRect = best.getBoundingClientRect();
  return {
    key: _agentPanelAnchorKey(best),
    offset: anchorRect.top - containerRect.top,
  };
}

function _agentPanelRestoreScrollAnchor(container, snapshot) {
  if (!container || !snapshot || !snapshot.key
      || typeof container.getBoundingClientRect !== 'function'
      || typeof container.scrollTop !== 'number') {
    return;
  }
  var items = _agentPanelAnchorItems(container);
  var target = null;
  for (var i = 0; i < items.length; i++) {
    if (_agentPanelAnchorKey(items[i]) === snapshot.key) {
      target = items[i];
      break;
    }
  }
  if (!target || typeof target.getBoundingClientRect !== 'function') return;
  var containerRect = container.getBoundingClientRect();
  var targetRect = target.getBoundingClientRect();
  container.scrollTop += (targetRect.top - containerRect.top) - (snapshot.offset || 0);
}

function _agentPanelEventsAtLiveTail(container) {
  // Agent-panel event lists are newest-first today, so the live tail is the
  // top edge.  Keep this intentionally separate from older-event pagination
  // (which loads near the bottom) so the current ordering semantics stay put.
  if (!container || typeof container.scrollTop !== 'number') return false;
  return container.scrollTop <= 4;
}

function _agentPanelEventsResetPager(agentId) {
  _agentPanelEventsPagerAgentId = String(agentId || '');
  _agentPanelEventsVisibleLimit = _AGENT_PANEL_EVENTS_PAGE_SIZE;
  _agentPanelEventsLastTotal = 0;
}

function _agentPanelEventsEnsurePager(agent) {
  var agentId = String((agent && agent.id) || '');
  if (agentId !== _agentPanelEventsPagerAgentId) {
    _agentPanelEventsResetPager(agentId);
  }
}

function _agentPanelEventCompareDesc(a, b) {
  a = a || {};
  b = b || {};
  var tsDiff = Number(b.timestamp || 0) - Number(a.timestamp || 0);
  if (tsDiff) return tsDiff;
  var aNum = Number(a.id);
  var bNum = Number(b.id);
  if (!Number.isNaN(aNum) && !Number.isNaN(bNum) && aNum !== bNum) {
    return bNum - aNum;
  }
  return String(b.id || '').localeCompare(String(a.id || ''));
}

function _agentPanelEventPage(agent, events) {
  _agentPanelEventsEnsurePager(agent);
  events = Array.isArray(events) ? events : [];
  var total = events.length;
  var added = total - _agentPanelEventsLastTotal;
  if (added > 0
      && _agentPanelEventsLastTotal > 0
      && !_agentPanelEventsPreRenderAtLiveTail) {
    // A focused-agent event landed above the user's viewport. Grow the render
    // window by the inserted rows so the previously visible older row stays in
    // the DOM for the shared anchor-restore helper to lock onto.
    _agentPanelEventsVisibleLimit += added;
  }
  _agentPanelEventsLastTotal = total;

  if (_agentPanelEventsVisibleLimit < _AGENT_PANEL_EVENTS_PAGE_SIZE) {
    _agentPanelEventsVisibleLimit = _AGENT_PANEL_EVENTS_PAGE_SIZE;
  }

  var visibleCount = Math.min(total, _agentPanelEventsVisibleLimit);
  return {
    events: events.slice(0, visibleCount),
    total: total,
    visibleCount: visibleCount,
    hasMore: visibleCount < total,
  };
}

function _agentPanelEventSectionCount(page) {
  if (!page || !page.total) return '0';
  if (page.hasMore) return page.visibleCount + ' / ' + page.total;
  return String(page.total);
}

function _agentPanelRenderEventLoadMore(page) {
  if (!page || !page.hasMore) return '';
  var remaining = Math.max(0, page.total - page.visibleCount);
  var nextCount = Math.min(_AGENT_PANEL_EVENTS_PAGE_SIZE, remaining);
  return '<button type="button" class="agent-panel-event-load-more"'
    + ' onclick="agentPanelLoadMoreEvents(event)">'
    + 'Load ' + nextCount + ' older event' + (nextCount === 1 ? '' : 's')
    + '</button>';
}

function _agentPanelSortedCellEvents(agent) {
  var events = _agentPanelCellEventsForAgent(agent);
  events.sort(_agentPanelEventCompareDesc);
  return events;
}

function _agentPanelSortedWorkerEvents(agent) {
  var agentId = String((agent && agent.id) || '');
  var events = (state && state.panel_events ? state.panel_events.slice() : []).filter(function(evt) {
    return String((evt && evt.cell_id) || '') === agentId;
  });
  events.sort(_agentPanelEventCompareDesc);
  return events;
}

function _agentPanelEventTotalForAgent(agent) {
  if (!agent) return 0;
  if (_agentPanelUsesMergedCellEvents(agent)) {
    return _agentPanelSortedCellEvents(agent).length;
  }
  if (_agentPanelKind(agent) === 'worker') {
    return _agentPanelSortedWorkerEvents(agent).length;
  }
  return 0;
}

function _agentPanelEventsNearOlderTail(container) {
  if (!container || typeof container.scrollTop !== 'number') return false;
  var scrollHeight = Number(container.scrollHeight || 0);
  var clientHeight = Number(container.clientHeight || 0);
  return scrollHeight - container.scrollTop - clientHeight < _AGENT_PANEL_EVENTS_SCROLL_THRESHOLD;
}

function _agentPanelAttachEventsScroll(root, agent) {
  if (!root || typeof root.querySelector !== 'function' || !agent) return;
  if (_agentPanelActiveTab(_agentPanelKind(agent)) !== 'events') return;
  if (!_agentPanelShouldAutoLoadEvents(agent)) return;
  var container = root.querySelector('.agent-panel-content');
  if (!container || typeof container.addEventListener !== 'function') return;
  if (typeof container.removeEventListener === 'function') {
    container.removeEventListener('scroll', agentPanelEventsOnScroll);
  }
  container.addEventListener('scroll', agentPanelEventsOnScroll);
}

function _agentPanelDetachEventsScroll(root) {
  if (!root || typeof root.querySelector !== 'function') return;
  var container = root.querySelector('.agent-panel-content');
  if (!container || typeof container.removeEventListener !== 'function') return;
  container.removeEventListener('scroll', agentPanelEventsOnScroll);
}

function agentPanelEventsOnScroll(evt) {
  var container = (evt && (evt.currentTarget || evt.target)) || this;
  if (!_agentPanelEventsNearOlderTail(container)) return;
  agentPanelLoadMoreEvents();
}

function agentPanelLoadMoreEvents(evt) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  var agent = _resolveFocusedAgent();
  if (!agent || _agentPanelActiveTab(_agentPanelKind(agent)) !== 'events') return;
  if (!_agentPanelShouldAutoLoadEvents(agent)) return;
  _agentPanelEventsEnsurePager(agent);
  var total = _agentPanelEventTotalForAgent(agent);
  if (_agentPanelEventsVisibleLimit >= total) return;
  _agentPanelEventsVisibleLimit = Math.min(
    total,
    _agentPanelEventsVisibleLimit + _AGENT_PANEL_EVENTS_PAGE_SIZE
  );
  renderAgentPanel();
}

function _agentPanelShouldAutoLoadEvents(agent) {
  if (!agent) return false;
  if (_agentPanelKind(agent) === 'worker') {
    return _agentPanelEventsInnerTab(agent) === 'lifecycle';
  }
  if (_agentPanelUsesMergedCellEvents(agent)) {
    return _agentPanelEventsInnerTab(agent) === 'lifecycle';
  }
  return false;
}

function _agentPanelSectionPagerKey(agentId, section) {
  return String(agentId || '') + '::' + String(section || '');
}

function _agentPanelSectionPage(agentId, section, events) {
  events = Array.isArray(events) ? events : [];
  var key = _agentPanelSectionPagerKey(agentId, section);
  var pager = _agentPanelSectionPagers[key];
  if (!pager) {
    pager = { visibleLimit: _AGENT_PANEL_EVENTS_PAGE_SIZE, lastTotal: 0 };
    _agentPanelSectionPagers[key] = pager;
  }
  var total = events.length;
  // Same anchor-preservation trick as the Cell events pager: when new events
  // land at the top (newest-first), grow the render window by the inserted
  // rows so the row the user was looking at stays in the DOM for the shared
  // anchor-restore helper.
  var added = total - pager.lastTotal;
  if (added > 0 && pager.lastTotal > 0 && !_agentPanelEventsPreRenderAtLiveTail) {
    pager.visibleLimit += added;
  }
  pager.lastTotal = total;
  if (pager.visibleLimit < _AGENT_PANEL_EVENTS_PAGE_SIZE) {
    pager.visibleLimit = _AGENT_PANEL_EVENTS_PAGE_SIZE;
  }
  var visibleCount = Math.min(total, pager.visibleLimit);
  return {
    section: String(section || ''),
    agentId: String(agentId || ''),
    events: events.slice(0, visibleCount),
    total: total,
    visibleCount: visibleCount,
    hasMore: visibleCount < total,
  };
}

function _agentPanelRenderSectionLoadMore(page, noun) {
  if (!page || !page.hasMore) return '';
  var remaining = Math.max(0, page.total - page.visibleCount);
  var nextCount = Math.min(_AGENT_PANEL_EVENTS_PAGE_SIZE, remaining);
  var section = _agentPanelEsc(page.section);
  var agentId = _agentPanelEsc(page.agentId);
  var singular = '';
  var plural = '';
  if (noun && typeof noun === 'object') {
    singular = String(noun.singular || '');
    plural = String(noun.plural || (singular ? singular + 's' : ''));
  } else {
    singular = String(noun || 'event');
    plural = singular + 's';
  }
  var label = nextCount === 1 ? singular : plural;
  return '<button type="button" class="agent-panel-event-load-more"'
    + ' data-agent-panel-section="' + section + '"'
    + ' data-agent-panel-section-agent="' + agentId + '"'
    + ' onclick="agentPanelLoadMoreSection(event, \'' + section
    + '\', \'' + agentId + '\')">'
    + 'Load ' + nextCount + ' older ' + label
    + '</button>';
}

function agentPanelLoadMoreSection(evt, section, agentId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  var resolvedId = String(agentId || '');
  if (!resolvedId) {
    var focused = _resolveFocusedAgent();
    resolvedId = focused ? String(focused.id || '') : '';
  }
  if (!resolvedId) return;
  var key = _agentPanelSectionPagerKey(resolvedId, section);
  var pager = _agentPanelSectionPagers[key];
  if (!pager) return;
  pager.visibleLimit += _AGENT_PANEL_EVENTS_PAGE_SIZE;
  renderAgentPanel();
}

function _agentPanelRenderTabs(kind, activeTab) {
  var tabs = _agentPanelTabSpec(kind);
  if (!tabs.length) return '';
  var html = '<div class="agent-panel-tabs">';
  for (var i = 0; i < tabs.length; i++) {
    var tab = tabs[i];
    html += '<button type="button"'
      + ' id="agent-panel-tab-' + _agentPanelEsc(tab.key) + '"'
      + ' class="agent-panel-tab' + (activeTab === tab.key ? ' active' : '') + '"'
      + ' data-agent-panel-tab-key="' + _agentPanelEsc(tab.key) + '"'
      + ' onclick="agentPanelSelectTab(\'' + _agentPanelEsc(tab.key) + '\')">'
      + _agentPanelEsc(tab.label)
      + '</button>';
  }
  html += '</div>';
  return html;
}


function _agentPanelShell(title, subtitle, kind, activeTab, bodyHtml, headerRightHtml, agentId, headerBreadcrumbHtml) {
  var html = '<div class="agent-panel-panel"';
  if (kind) html += ' data-agent-panel-kind="' + _agentPanelEsc(kind) + '"';
  if (activeTab) html += ' data-agent-panel-tab="' + _agentPanelEsc(activeTab) + '"';
  if (agentId) html += ' data-agent-panel-agent-id="' + _agentPanelEsc(agentId) + '"';
  html += '>';
  html += '<div class="agent-panel-header">';
  if (headerBreadcrumbHtml) {
    html += '<div class="agent-panel-header-breadcrumb" data-agent-panel-header-breadcrumb>'
      + headerBreadcrumbHtml
      + '</div>';
  }
  html += '<div class="agent-panel-header-copy">';
  html += '<span class="agent-panel-title">' + _agentPanelEsc(title || 'Agent') + '</span>';
  if (subtitle) {
    html += '<div class="agent-panel-subtitle">' + _agentPanelEsc(subtitle) + '</div>';
  }
  html += '</div>';
  html += '<div class="agent-panel-header-right" data-agent-panel-header-right>'
    + (headerRightHtml || '')
    + '</div>';
  html += '</div>';
  html += _agentPanelRenderTabs(kind, activeTab);
  html += '<div class="agent-panel-content">' + (bodyHtml || '') + '</div>';
  html += '</div>';
  return html;
}

function _agentPanelEngineerSettings(group) {
  if (typeof _engineerGetSettings === 'function') return _engineerGetSettings(group);
  return (state && state.engineer_settings && group) ? (state.engineer_settings[group] || null) : null;
}

function _agentPanelEngineerAgent(group) {
  if (typeof _engineerGetAgent === 'function') return _engineerGetAgent(group);
  if (!group || !state || !state.group_settings || !state.agents) return null;
  var settings = state.group_settings[group];
  return settings && settings.engineer_agent_id ? (state.agents[settings.engineer_agent_id] || null) : null;
}

function _agentPanelDigestSettings(agent) {
  var agentId = String((agent && agent.id) || '');
  if (!agentId || !state) return null;
  if (state.agent_digest_settings && state.agent_digest_settings[agentId]) {
    return state.agent_digest_settings[agentId];
  }
  var group = String((agent && agent.group) || '');
  var legacyEngineer = group ? _agentPanelEngineerAgent(group) : null;
  if (legacyEngineer && String(legacyEngineer.id || '') === agentId) {
    return _agentPanelEngineerSettings(group);
  }
  return null;
}

function _agentPanelDigestBufferStats(agent) {
  var agentId = String((agent && agent.id) || '');
  if (!agentId || !state) return null;
  if (state.digest_buffer_stats && state.digest_buffer_stats[agentId]) {
    return state.digest_buffer_stats[agentId];
  }
  var group = String((agent && agent.group) || '');
  var legacyEngineer = group ? _agentPanelEngineerAgent(group) : null;
  if (
    legacyEngineer
    && String(legacyEngineer.id || '') === agentId
    && state.engineer_buffer_stats
    && state.engineer_buffer_stats[group]
  ) {
    return state.engineer_buffer_stats[group];
  }
  return null;
}

function _agentPanelDigestSentEvents(agent) {
  var agentId = String((agent && agent.id) || '');
  if (!agentId || !state) return [];
  if (state.digest_sent_events && state.digest_sent_events[agentId]) {
    return state.digest_sent_events[agentId].slice();
  }
  var group = String((agent && agent.group) || '');
  var legacyEngineer = group ? _agentPanelEngineerAgent(group) : null;
  if (
    legacyEngineer
    && String(legacyEngineer.id || '') === agentId
    && state.engineer_sent_events
    && state.engineer_sent_events[group]
  ) {
    return state.engineer_sent_events[group].slice();
  }
  return [];
}

function _agentPanelDigestQueuedEvents(bstats) {
  if (!bstats) return [];
  if (Array.isArray(bstats.queued_events)) return bstats.queued_events.slice();
  // Older/local snapshots used a couple of transitional names while the
  // per-recipient digest state was split out of the legacy engineer settings.
  // Normalize them here so the Events tab can render a real queue whenever the
  // backend has published one, instead of showing an empty card next to a
  // non-zero buffered count.
  if (Array.isArray(bstats.events)) return bstats.events.slice();
  if (Array.isArray(bstats.buffered_event_list)) return bstats.buffered_event_list.slice();
  if (bstats.queued_events && typeof bstats.queued_events === 'object') {
    return Object.keys(bstats.queued_events).map(function(key) {
      return bstats.queued_events[key];
    }).filter(Boolean);
  }
  return [];
}

function _agentPanelDigestPauseButton(agent) {
  var agentId = String((agent && agent.id) || '');
  if (!agentId) return '';
  var settings = _agentPanelDigestSettings(agent);
  var paused = !!(settings && settings.paused);
  return '<button id="agent-panel-pause-btn" class="agent-panel-pause-btn'
    + (paused ? ' paused' : '')
    + '" onclick="agentPanelTogglePauseForAgent(\'' + _agentPanelEsc(agentId) + '\')">'
    + (paused ? '&#x25B6;' : '&#x23F8;')
    + '</button>';
}

function _agentPanelDigestHeaderRight(agent) {
  if (!agent) return '';
  var bstats = _agentPanelDigestBufferStats(agent);
  var settings = _agentPanelDigestSettings(agent);
  var paused = !!(settings && settings.paused);
  var html = '';
  if (bstats && bstats.buffered_events > 0) {
    html += '<span class="agent-panel-buffer-stats">'
      + _agentPanelEsc(_engineerHeaderBufferStats(bstats, paused, agent))
      + '</span>';
  }
  html += _agentPanelDigestPauseButton(agent);
  return html;
}

function _agentPanelRenderEventsTab(bstats, sentEvents, paused, recipient, sendNowExpr, sentTitle) {
  var queued = _agentPanelDigestQueuedEvents(bstats);
  var sent = Array.isArray(sentEvents) ? sentEvents.slice() : [];
  var sendDisabled = paused || !queued.length;
  var statusText = _engineerEventsStatusText(bstats, paused, recipient);

  sent.sort(function(a, b) {
    var deliveredDiff = (b.delivered_at || 0) - (a.delivered_at || 0);
    if (deliveredDiff) return deliveredDiff;
    return (b.id || 0) - (a.id || 0);
  });

  // Queued and Sent are digest-scoped lists (events forwarded from child cells
  // to this recipient). They are semantically distinct from "Cell events"
  // which lists this cell's own lifecycle. Both sections cap at 20 + explicit
  // "Load 20 older events" click to avoid unbounded panels on long histories.
  var recipientId = (recipient && recipient.id) || '';
  var queuedPage = _agentPanelSectionPage(recipientId, 'queued', queued);
  var sentPage = _agentPanelSectionPage(recipientId, 'sent', sent);

  var html = '<div class="agent-panel-events-inbox" data-agent-panel-events-panel="inbox">';
  html += '<div class="agent-panel-events-toolbar">';
  html += '<div class="agent-panel-events-countdown">' + _esc(statusText) + '</div>';
  html += '<button id="engineer-send-now-btn" class="agent-panel-send-now-btn"'
    + (sendDisabled ? ' disabled' : '')
    + ' onclick="' + _agentPanelEsc(sendNowExpr || 'engineerSendNow()') + '">Send queued now</button>';
  html += '</div>';
  html += _agentPanelRenderPagedEventSection(
    'Queued for next digest',
    queuedPage,
    'queued',
    'No queued events.'
  );
  html += _agentPanelRenderPagedEventSection(
    sentTitle || 'Already sent to Engineer',
    sentPage,
    'sent',
    'No digested events yet.'
  );
  html += '</div>';
  return html;
}

function _agentPanelRenderEventsInnerTabs(agent) {
  var active = _agentPanelEventsInnerTab(agent);
  var tabs = [
    { key: 'inbox', label: 'Inbox' },
    { key: 'lifecycle', label: 'Lifecycle' },
    { key: 'mcp', label: 'MCP' },
  ];
  var html = '<div class="agent-panel-events-subtabs" role="tablist" aria-label="Events views">';
  for (var i = 0; i < tabs.length; i++) {
    var tab = tabs[i];
    html += '<button type="button"'
      + ' id="agent-panel-events-subtab-' + _agentPanelEsc(tab.key) + '"'
      + ' class="agent-panel-events-subtab' + (active === tab.key ? ' active' : '') + '"'
      + ' data-agent-panel-events-inner-tab="' + _agentPanelEsc(tab.key) + '"'
      + ' role="tab"'
      + ' aria-selected="' + (active === tab.key ? 'true' : 'false') + '"'
      + ' onclick="agentPanelSelectEventsInnerTab(\'' + _agentPanelEsc(tab.key) + '\')">'
      + _agentPanelEsc(tab.label)
      + '</button>';
  }
  html += '</div>';
  return html;
}

function _renderAgentEventsWithInnerTabs(agent) {
  var active = _agentPanelEventsInnerTab(agent);
  var html = '<div class="agent-panel-events-tab" data-agent-panel-events-view="'
    + _agentPanelEsc(active) + '">';
  html += _agentPanelRenderEventsInnerTabs(agent);
  if (active === 'mcp') {
    html += _renderAgentMcpTab(agent);
  } else if (active === 'lifecycle') {
    html += '<div class="agent-panel-events-lifecycle" data-agent-panel-events-panel="lifecycle">'
      + (_agentPanelKind(agent) === 'worker'
        ? _agentPanelWorkerEvents(agent)
        : _renderPersistentCellEvents(agent))
      + '</div>';
  } else {
    html += _renderAgentDigestEvents(agent);
  }
  html += '</div>';
  return html;
}

function _agentPanelRenderPagedEventSection(title, page, mode, emptyText) {
  var total = (page && page.total) || 0;
  var events = (page && page.events) || [];
  var html = '<div class="agent-panel-event-section">';
  html += '<div class="agent-panel-event-section-header">';
  html += '<span class="agent-panel-event-section-title">' + _esc(title) + '</span>';
  html += '<span class="agent-panel-event-section-count">'
    + _agentPanelEventSectionCount(page) + '</span>';
  html += '</div>';
  if (!total) {
    html += '<div class="agent-panel-event-empty">' + _esc(emptyText) + '</div>';
    html += '</div>';
    return html;
  }
  html += '<div class="agent-panel-event-list">';
  for (var i = 0; i < events.length; i++) {
    html += _agentPanelLegacyRenderEventItem(events[i], mode);
  }
  html += '</div>';
  html += _agentPanelRenderSectionLoadMore(page);
  html += '</div>';
  return html;
}

function _agentPanelDecisionStores() {
  var stores = [];
  if (state && state.decisions) stores.push(state.decisions);
  if (state && state.architect_decisions && state.architect_decisions !== state.decisions) {
    stores.push(state.architect_decisions);
  }
  return stores;
}

function _agentPanelStoreRefsEqual(a, b) {
  a = Array.isArray(a) ? a : [];
  b = Array.isArray(b) ? b : [];
  if (a.length !== b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function _agentPanelInvalidateArchitectDecisionCache(architectId) {
  var key = String(architectId || '').trim();
  if (!key) {
    _agentPanelDecisionListCacheByArchitect = {};
    _agentPanelDecisionRowsCacheByArchitect = {};
    return;
  }
  delete _agentPanelDecisionListCacheByArchitect[key];
  delete _agentPanelDecisionRowsCacheByArchitect[key];
  delete _agentPanelDecisionRowsCacheByArchitect[key + ':0'];
  delete _agentPanelDecisionRowsCacheByArchitect[key + ':1'];
}

function _agentPanelInvalidateArchitectMessageCache(agentId) {
  var key = String(agentId || '').trim();
  if (!key) {
    _agentPanelMessageListCacheByArchitect = {};
    return;
  }
  delete _agentPanelMessageListCacheByArchitect[key];
}

function _agentPanelInvalidateArchitectPeerListCache(architectId) {
  var key = String(architectId || '').trim();
  if (!key) {
    _agentPanelArchitectPeerListByArchitect = {};
    _agentPanelArchitectPeerListRequestedByArchitect = {};
    _agentPanelPruneArchitectPeerDrafts();
    return;
  }
  delete _agentPanelArchitectPeerListByArchitect[key];
  delete _agentPanelArchitectPeerListRequestedByArchitect[key];
  _agentPanelPruneArchitectPeerDraft(key);
}

function _agentPanelInvalidateArchitectJournalCache(architectId) {
  var key = String(architectId || '').trim();
  if (!key) {
    _agentPanelArchitectJournalByArchitect = {};
    return;
  }
  delete _agentPanelArchitectJournalByArchitect[key];
}

function _agentPanelArchitectJournalVisibleLimit(architectId) {
  architectId = String(architectId || '').trim();
  if (!architectId) return _AGENT_PANEL_JOURNAL_PAGE_SIZE;
  var visible = Number(_agentPanelArchitectJournalVisibleLimitById[architectId] || 0);
  if (!visible || visible < _AGENT_PANEL_JOURNAL_PAGE_SIZE) {
    visible = _AGENT_PANEL_JOURNAL_PAGE_SIZE;
    _agentPanelArchitectJournalVisibleLimitById[architectId] = visible;
  }
  return visible;
}

function _agentPanelSetArchitectJournalVisibleLimit(architectId, limit) {
  architectId = String(architectId || '').trim();
  if (!architectId) return _AGENT_PANEL_JOURNAL_PAGE_SIZE;
  var value = Math.max(
    _AGENT_PANEL_JOURNAL_PAGE_SIZE,
    Number(limit || 0) || _AGENT_PANEL_JOURNAL_PAGE_SIZE
  );
  _agentPanelArchitectJournalVisibleLimitById[architectId] = value;
  return value;
}

function _agentPanelArchitectJournalEntries(agent) {
  var architectId = String((agent && agent.id) || '').trim();
  if (!architectId) return [];
  if (state && state.architect_journals
      && Array.isArray(state.architect_journals[architectId])) {
    return state.architect_journals[architectId];
  }
  return [];
}

function _agentPanelArchitectJournalLatestCheckpoint(entries) {
  entries = Array.isArray(entries) ? entries : [];
  for (var i = 0; i < entries.length; i++) {
    var entry = entries[i] || {};
    if (String(entry.type || '').toLowerCase() === 'checkpoint') return entry;
  }
  return null;
}

function _agentPanelArchitectJournalMergeEntries(current, incoming) {
  var merged = [];
  var seen = {};
  function keyFor(entry, index) {
    entry = entry || {};
    var id = String(entry.id || '').trim();
    if (id) return 'id:' + id;
    return 'fallback:' + String(entry.timestamp || '') + ':'
      + String(entry.type || '') + ':' + String(entry.entry || '') + ':' + index;
  }
  function addAll(entries) {
    entries = Array.isArray(entries) ? entries : [];
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i];
      if (!entry) continue;
      var key = keyFor(entry, i);
      if (seen[key]) continue;
      seen[key] = true;
      merged.push(entry);
    }
  }
  addAll(incoming);
  addAll(current);
  merged.sort(function(a, b) {
    var tsDiff = Number((b && b.timestamp) || 0) - Number((a && a.timestamp) || 0);
    if (tsDiff) return tsDiff;
    return String((b && b.id) || '').localeCompare(String((a && a.id) || ''));
  });
  return merged;
}

function _agentPanelRequestArchitectJournal(agent, options) {
  options = options || {};
  var architectId = String((agent && agent.id) || '').trim();
  if (!architectId) return;
  if (typeof send !== 'function') return;
  if (_agentPanelArchitectJournalLoadingById[architectId]) return;
  var currentEntries = _agentPanelArchitectJournalEntries(agent);
  var visibleLimit = _agentPanelArchitectJournalVisibleLimit(architectId);
  var requestedLimit = Math.max(
    _AGENT_PANEL_JOURNAL_PAGE_SIZE,
    visibleLimit,
    Number(options.limit || 0) || 0,
    Number(_agentPanelArchitectJournalRequestedLimitById[architectId] || 0) || 0
  );
  if (!options.force && currentEntries.length >= requestedLimit) return;
  if (!options.force && _agentPanelArchitectJournalExhaustedById[architectId]) return;
  var now = Date.now();
  var lastFetch = Number(_agentPanelArchitectJournalLastFetchById[architectId] || 0);
  var hasCache = !!(state && state.architect_journals
    && Array.isArray(state.architect_journals[architectId]));
  if (!options.force && hasCache && lastFetch
      && (now - lastFetch) < _AGENT_PANEL_JOURNAL_REFRESH_MS) return;
  _agentPanelArchitectJournalLoadingById[architectId] = true;
  _agentPanelArchitectJournalRequestedLimitById[architectId] = requestedLimit;
  _agentPanelArchitectJournalInFlightLimitById[architectId] = requestedLimit;
  _agentPanelArchitectJournalLastFetchById[architectId] = now;
  send({
    cmd: 'architect_journal_read',
    architect_id: architectId,
    limit: requestedLimit,
  });
}

function agentPanelReceiveArchitectJournal(data) {
  var architectId = String((data && data.architect_id) || '').trim();
  if (!architectId) return;
  if (!state.architect_journals) state.architect_journals = {};
  var incomingEntries = Array.isArray(data.entries)
    ? data.entries.slice()
    : [];
  var responseLimit = Number((data && data.limit) || 0)
    || Number(_agentPanelArchitectJournalInFlightLimitById[architectId] || 0)
    || Number(_agentPanelArchitectJournalRequestedLimitById[architectId] || 0)
    || _AGENT_PANEL_JOURNAL_PAGE_SIZE;
  _agentPanelArchitectJournalRequestedLimitById[architectId] = Math.max(
    _AGENT_PANEL_JOURNAL_PAGE_SIZE,
    Number(_agentPanelArchitectJournalRequestedLimitById[architectId] || 0) || 0,
    responseLimit
  );
  state.architect_journals[architectId] = _agentPanelArchitectJournalMergeEntries(
    state.architect_journals[architectId],
    incomingEntries
  );
  var hasDecisionEntry = incomingEntries.some(function(entry) {
    return String((entry && entry.type) || '').toLowerCase() === 'decision';
  });
  _agentPanelArchitectJournalExhaustedById[architectId] =
    incomingEntries.length < responseLimit;
  _agentPanelArchitectJournalLoadingById[architectId] = false;
  delete _agentPanelArchitectJournalInFlightLimitById[architectId];
  _agentPanelArchitectJournalLastFetchById[architectId] = Date.now();
  _agentPanelInvalidateArchitectJournalCache(architectId);
  if (hasDecisionEntry && typeof renderInvalidatedSurfaces === 'function') {
    renderInvalidatedSurfaces({ main: true });
  }
  var focused = _resolveFocusedAgent();
  if (focused && String(focused.id || '') === architectId
      && _agentPanelKind(focused) === 'architect'
      && _agentPanelActiveTab('architect') === 'journal') {
    if (typeof _agentPanelRefreshCurrentTab === 'function'
        && _agentPanelRefreshCurrentTab()) return;
    if (typeof renderAgentPanel === 'function') renderAgentPanel();
  }
}

function _agentPanelArchitectJournalCanLoadOlder(architectId, entries, visibleLimit) {
  architectId = String(architectId || '').trim();
  entries = Array.isArray(entries) ? entries : [];
  visibleLimit = Math.max(
    _AGENT_PANEL_JOURNAL_PAGE_SIZE,
    Number(visibleLimit || 0) || _AGENT_PANEL_JOURNAL_PAGE_SIZE
  );
  if (visibleLimit < entries.length) return true;
  if (_agentPanelArchitectJournalLoadingById[architectId]) return false;
  if (_agentPanelArchitectJournalExhaustedById[architectId]) return false;
  var requestedLimit = Number(_agentPanelArchitectJournalRequestedLimitById[architectId] || 0);
  if (!requestedLimit) return false;
  return entries.length >= requestedLimit;
}

function _agentPanelArchitectJournalDidPrepend(architectId) {
  architectId = String(architectId || '').trim();
  if (!architectId) return;
  var visible = _agentPanelArchitectJournalVisibleLimit(architectId);
  var nextVisible = _agentPanelSetArchitectJournalVisibleLimit(
    architectId,
    visible + 1
  );
  _agentPanelArchitectJournalRequestedLimitById[architectId] = Math.max(
    Number(_agentPanelArchitectJournalRequestedLimitById[architectId] || 0) || 0,
    nextVisible
  );
}

function _agentPanelArchitectJournalLoadMoreLabel(entries, visibleLimit) {
  entries = Array.isArray(entries) ? entries : [];
  visibleLimit = Math.max(
    _AGENT_PANEL_JOURNAL_PAGE_SIZE,
    Number(visibleLimit || 0) || _AGENT_PANEL_JOURNAL_PAGE_SIZE
  );
  var hiddenLoaded = Math.max(0, entries.length - visibleLimit);
  var count = hiddenLoaded
    ? Math.min(_AGENT_PANEL_JOURNAL_PAGE_SIZE, hiddenLoaded)
    : _AGENT_PANEL_JOURNAL_PAGE_SIZE;
  return 'Load ' + count + ' older entr' + (count === 1 ? 'y' : 'ies');
}

function agentPanelLoadOlderArchitectJournal(evt, architectId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  var resolvedId = String(architectId || '').trim();
  var agent = null;
  if (resolvedId && state && state.agents) agent = state.agents[resolvedId] || null;
  if (!agent) {
    agent = _resolveFocusedAgent();
    resolvedId = String((agent && agent.id) || resolvedId || '').trim();
  }
  if (!resolvedId || !agent) return;
  var entries = _agentPanelArchitectJournalEntries(agent);
  var currentVisible = _agentPanelArchitectJournalVisibleLimit(resolvedId);
  var nextVisible = _agentPanelSetArchitectJournalVisibleLimit(
    resolvedId,
    currentVisible + _AGENT_PANEL_JOURNAL_PAGE_SIZE
  );
  if (entries.length < nextVisible && !_agentPanelArchitectJournalExhaustedById[resolvedId]) {
    _agentPanelRequestArchitectJournal(agent, { force: true, limit: nextVisible });
  }
  if (typeof _agentPanelRefreshCurrentTab === 'function'
      && _agentPanelRefreshCurrentTab()) return;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function _agentPanelMessageCompareDesc(a, b) {
  var aTs = Number((a && a.timestamp) || 0);
  var bTs = Number((b && b.timestamp) || 0);
  if (aTs !== bTs) return bTs - aTs;
  return 0;
}

function _agentPanelDecisionIsArchived(decision) {
  return !!(decision && decision.archived);
}

function _agentPanelArchitectDecisionList(agentId, opts) {
  opts = opts || {};
  var stores = [];
  var architectId = String(agentId || '');
  if (!architectId) return [];
  stores = _agentPanelDecisionStores();
  var cached = _agentPanelDecisionListCacheByArchitect[architectId];
  var allItems = null;
  var activeItems = null;
  if (cached && _agentPanelStoreRefsEqual(cached.stores, stores)) {
    allItems = cached.items;
    activeItems = cached.activeItems;
  } else {
    var results = [];
    var seen = {};
    for (var storeIndex = 0; storeIndex < stores.length; storeIndex++) {
      var store = stores[storeIndex] || {};
      var values = Array.isArray(store)
        ? store
        : Object.keys(store).map(function(key) { return store[key]; });
      for (var valueIndex = 0; valueIndex < values.length; valueIndex++) {
        var decision = values[valueIndex];
        if (!decision) continue;
        if (String(decision.architect_id || '') !== architectId) continue;
        var decisionId = String(decision.id || '');
        var seenKey = decisionId || ('idx:' + storeIndex + ':' + valueIndex);
        if (seen[seenKey]) continue;
        seen[seenKey] = true;
        results.push(decision);
      }
    }
    results.sort(_engineerDecisionRecencySort);
    activeItems = results.filter(function(decision) {
      return !_agentPanelDecisionIsArchived(decision);
    });
    _agentPanelDecisionListCacheByArchitect[architectId] = {
      stores: stores.slice(),
      items: results,
      activeItems: activeItems,
    };
    allItems = results;
  }
  if (opts.include_archived) return allItems;
  if (activeItems) return activeItems;
  return allItems.filter(function(decision) {
    return !_agentPanelDecisionIsArchived(decision);
  });
}

function _agentPanelArchitectDecisions(agentId) {
  return _agentPanelArchitectDecisionList(agentId).slice();
}

function _agentPanelArchitectDecisionCounts(agentId) {
  var decisions = _agentPanelArchitectDecisionList(agentId, { include_archived: true });
  var archived = 0;
  for (var i = 0; i < decisions.length; i++) {
    if (_agentPanelDecisionIsArchived(decisions[i])) archived += 1;
  }
  return {
    active: Math.max(0, decisions.length - archived),
    archived: archived,
    total: decisions.length,
  };
}

function _agentPanelShowArchivedDecisions(agentId) {
  var key = String(agentId || '').trim();
  return !!(key && _agentPanelShowArchivedDecisionsByArchitect[key]);
}

function agentPanelToggleArchivedDecisions(evt, architectId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  var key = String(architectId || '').trim();
  if (!key) {
    var focused = _resolveFocusedAgent();
    key = String((focused && focused.id) || '').trim();
  }
  if (!key) return;
  _agentPanelShowArchivedDecisionsByArchitect[key] = !_agentPanelShowArchivedDecisionsByArchitect[key];
  if (_agentPanelShowArchivedDecisionsByArchitect[key]
      && typeof lazyLoadDecisions === 'function') {
    lazyLoadDecisions({ include_archived: true });
  }
  if (typeof _agentPanelRefreshCurrentTab === 'function'
      && _agentPanelRefreshCurrentTab()) return;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function _agentPanelArchitectDecisionRowsForAgent(agentId, opts) {
  opts = opts || {};
  var architectId = String(agentId || '');
  if (!architectId) return [];
  var includeArchived = !!opts.include_archived;
  var decisions = _agentPanelArchitectDecisionList(architectId, {
    include_archived: includeArchived,
  });
  var cacheKey = architectId + ':' + (includeArchived ? '1' : '0');
  var cached = _agentPanelDecisionRowsCacheByArchitect[cacheKey];
  if (cached && cached.decisions === decisions) return cached.rows;
  var rows = _agentPanelArchitectDecisionRows(decisions);
  _agentPanelDecisionRowsCacheByArchitect[cacheKey] = {
    decisions: decisions,
    rows: rows,
  };
  return rows;
}

function _agentPanelArchitectMessageList(agent) {
  var agentId = String((agent && agent.id) || '');
  if (!agentId) return [];
  var source = Array.isArray(agent && agent.mcp_messages) ? agent.mcp_messages : [];
  var cached = _agentPanelMessageListCacheByArchitect[agentId];
  if (cached && cached.source === source && cached.length === source.length) {
    return cached.items;
  }
  var messages = source.map(function(message, index) {
    return { message: message, index: index };
  }).sort(function(a, b) {
    var diff = _agentPanelMessageCompareDesc(a.message, b.message);
    return diff || (a.index - b.index);
  }).map(function(item) {
    return item.message;
  });
  _agentPanelMessageListCacheByArchitect[agentId] = {
    source: source,
    length: source.length,
    items: messages,
  };
  return messages;
}

function _agentPanelArchitectPeerListFromState(agent) {
  var architectId = String((agent && agent.id) || '').trim();
  var group = String((agent && agent.group) || '').trim();
  var peers = [];
  if (!architectId || !state || !state.agents) return peers;
  for (var id in state.agents) {
    var peer = state.agents[id];
    if (!peer || String(peer.id || id) === architectId) continue;
    if (String(peer.cell_type || '') !== 'agent') continue;
    if (String(peer.kind || '') !== 'architect') continue;
    if (group && String(peer.group || '') !== group) continue;
    if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(peer)) continue;
    if (Number(peer.dismissed_at || 0) > 0) continue;
    peers.push({
      id: peer.id || id,
      name: _agentPanelAgentDisplayName(peer, peer.id || id),
      slug: peer.slug || '',
      group: peer.group || group,
      status: peer.status || '',
      dismissed: Number(peer.dismissed_at || 0) > 0,
    });
  }
  peers.sort(function(a, b) {
    return String(a.name || a.id).toLowerCase()
      .localeCompare(String(b.name || b.id).toLowerCase())
      || String(a.id || '').localeCompare(String(b.id || ''));
  });
  return peers;
}

function _agentPanelArchitectPeerIsSelectable(agent, peer) {
  var architectId = String((agent && agent.id) || '').trim();
  var group = String((agent && agent.group) || '').trim();
  var peerId = String((peer && peer.id) || '').trim();
  if (!architectId || !peerId || peerId === architectId) return false;
  var livePeer = null;
  var hasLiveAgentIndex = !!(state && state.agents && Object.keys(state.agents).length);
  if (hasLiveAgentIndex) {
    livePeer = state.agents[peerId] || null;
    if (!livePeer) return false;
  }
  var candidate = livePeer || peer || {};
  if (livePeer) {
    if (String(candidate.cell_type || '') !== 'agent') return false;
    if (String(candidate.kind || '') !== 'architect') return false;
  }
  if (group && String(candidate.group || (peer && peer.group) || '') !== group) return false;
  if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(candidate)) return false;
  if (Number(candidate.dismissed_at || (peer && peer.dismissed_at) || 0) > 0) return false;
  return true;
}

function _agentPanelFilterArchitectPeerList(agent, peers) {
  peers = Array.isArray(peers) ? peers : [];
  return peers.filter(function(peer) {
    return _agentPanelArchitectPeerIsSelectable(agent, peer);
  });
}

function _agentPanelEnsureArchitectPeerList(agent) {
  var architectId = String((agent && agent.id) || '').trim();
  if (!architectId || _agentPanelArchitectPeerListRequestedByArchitect[architectId]) return;
  if (typeof send !== 'function') return;
  _agentPanelArchitectPeerListRequestedByArchitect[architectId] = true;
  send({
    cmd: 'architect_peer_list',
    architect_id: architectId,
  });
}

function _agentPanelArchitectPeerList(agent) {
  var architectId = String((agent && agent.id) || '').trim();
  _agentPanelEnsureArchitectPeerList(agent);
  var cached = architectId ? _agentPanelArchitectPeerListByArchitect[architectId] : null;
  if (Array.isArray(cached)) {
    return _agentPanelFilterArchitectPeerList(agent, cached).map(function(peer) {
      return Object.assign({}, peer || {});
    });
  }
  return _agentPanelArchitectPeerListFromState(agent);
}

function agentPanelReceiveArchitectPeerList(msg) {
  msg = msg || {};
  var architectId = String(
    msg.architect_id || msg.caller_architect_id || msg.caller_id || ''
  ).trim();
  if (!architectId) {
    var focused = _resolveFocusedAgent();
    if (focused && _agentPanelKind(focused) === 'architect') {
      architectId = String(focused.id || '').trim();
    }
  }
  if (!architectId) return;
  var peers = Array.isArray(msg.architects) ? msg.architects : [];
  _agentPanelArchitectPeerListByArchitect[architectId] = peers.map(function(peer) {
    return Object.assign({}, peer || {});
  });
  var focusedAgent = _resolveFocusedAgent();
  if (focusedAgent
      && String(focusedAgent.id || '') === architectId
      && _agentPanelActiveTab('architect') === 'messages') {
    if (typeof _agentPanelRefreshCurrentTab === 'function'
        && _agentPanelRefreshCurrentTab()) return;
    if (typeof renderAgentPanel === 'function') renderAgentPanel();
  }
}

function _agentPanelPeerDraft(architectId) {
  var key = String(architectId || '').trim();
  if (!_agentPanelArchitectPeerComposeDrafts[key]) {
    _agentPanelArchitectPeerComposeDrafts[key] = {
      peer_id: '',
      message: '',
      ack_required: false,
      context_task_ids: '',
      context_engineer_ids: '',
      context_decision_ids: '',
      context_summary: '',
    };
  }
  return _agentPanelArchitectPeerComposeDrafts[key];
}

function _agentPanelPruneArchitectPeerDraft(architectId) {
  var key = String(architectId || '').trim();
  var draft = key ? _agentPanelArchitectPeerComposeDrafts[key] : null;
  if (!draft || !draft.peer_id) return false;
  var agent = (state && state.agents) ? state.agents[key] : null;
  if (agent && _agentPanelArchitectPeerIsSelectable(agent, { id: draft.peer_id })) {
    return false;
  }
  draft.peer_id = '';
  return true;
}

function _agentPanelPruneArchitectPeerDrafts() {
  for (var architectId in _agentPanelArchitectPeerComposeDrafts) {
    _agentPanelPruneArchitectPeerDraft(architectId);
  }
}

function _agentPanelPruneArchitectPeerDraftForList(architectId, peers) {
  var key = String(architectId || '').trim();
  var draft = key ? _agentPanelArchitectPeerComposeDrafts[key] : null;
  if (!draft || !draft.peer_id) return false;
  var selected = String(draft.peer_id || '').trim();
  peers = Array.isArray(peers) ? peers : [];
  for (var i = 0; i < peers.length; i++) {
    if (String((peers[i] && peers[i].id) || '').trim() === selected) return false;
  }
  draft.peer_id = '';
  return true;
}

function _agentPanelSplitContextIds(value) {
  var seen = {};
  return String(value || '')
    .split(/[,\n]/)
    .map(function(item) { return item.trim(); })
    .filter(function(item) {
      if (!item || seen[item]) return false;
      seen[item] = true;
      return true;
    });
}

function agentPanelPeerComposeInput(architectId, field, value) {
  var draft = _agentPanelPeerDraft(architectId);
  field = String(field || '');
  if (field === 'peer_id'
      || field === 'message'
      || field === 'context_task_ids'
      || field === 'context_engineer_ids'
      || field === 'context_decision_ids'
      || field === 'context_summary') {
    draft[field] = String(value || '');
  }
}

function agentPanelPeerComposeToggle(architectId, checked) {
  _agentPanelPeerDraft(architectId).ack_required = !!checked;
}

function _agentPanelPeerComposePayload(architectId) {
  var draft = _agentPanelPeerDraft(architectId);
  return {
    cmd: 'architect_peer_message',
    architect_id: String(architectId || ''),
    peer_architect_id: String(draft.peer_id || '').trim(),
    recipient_architect_id: String(draft.peer_id || '').trim(),
    message: String(draft.message || '').trim(),
    ack_required: !!draft.ack_required,
    context_task_ids: _agentPanelSplitContextIds(draft.context_task_ids),
    context_engineer_ids: _agentPanelSplitContextIds(draft.context_engineer_ids),
    context_decision_ids: _agentPanelSplitContextIds(draft.context_decision_ids),
    context_summary: String(draft.context_summary || '').trim(),
  };
}

function agentPanelPeerComposeSubmit(evt, architectId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  var payload = _agentPanelPeerComposePayload(architectId);
  if (!payload.architect_id || !payload.peer_architect_id) {
    if (typeof _showToast === 'function') _showToast('Choose a peer Architect', 'warning');
    return false;
  }
  if (!payload.message) {
    if (typeof _showToast === 'function') _showToast('Message is required', 'warning');
    return false;
  }
  payload.architect_id = payload.peer_architect_id;
  delete payload.peer_architect_id;
  delete payload.recipient_architect_id;
  payload.sender_architect_id = String(architectId || '');
  payload.idempotency_key = 'ui-peer-' + String(architectId || '')
    + '-' + Date.now() + '-' + Math.random().toString(16).slice(2);
  if (typeof send === 'function') send(payload);
  var draft = _agentPanelPeerDraft(architectId);
  draft.message = '';
  draft.ack_required = false;
  draft.context_task_ids = '';
  draft.context_engineer_ids = '';
  draft.context_decision_ids = '';
  draft.context_summary = '';
  if (typeof _showToast === 'function') _showToast('Peer message queued', 'success');
  if (typeof _agentPanelRefreshCurrentTab === 'function'
      && _agentPanelRefreshCurrentTab()) return false;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
  return false;
}

function _agentPanelArchitectPeerComposeHtml(agent) {
  var architectId = String((agent && agent.id) || '').trim();
  if (!architectId) return '';
  var draft = _agentPanelPeerDraft(architectId);
  var peers = _agentPanelArchitectPeerList(agent);
  _agentPanelPruneArchitectPeerDraftForList(architectId, peers);
  var safeId = _agentPanelDomIdToken(architectId);
  var architectIdJs = _agentPanelJsString(architectId);
  var html = '<form class="agent-panel-peer-compose" onsubmit="'
    + _agentPanelEventAttr('return agentPanelPeerComposeSubmit(event,' + architectIdJs + ')')
    + '">';
  html += '<div class="agent-panel-peer-compose-head">';
  html += '<label class="agent-panel-peer-compose-field">'
    + '<span>Peer Architect</span>'
    + '<select id="agent-panel-peer-select-' + _agentPanelAttr(safeId) + '"'
    + ' class="agent-panel-peer-select"'
    + ' onchange="' + _agentPanelEventAttr('agentPanelPeerComposeInput('
      + architectIdJs + ', "peer_id", this.value)') + '">';
  html += '<option value="">Select peer…</option>';
  for (var i = 0; i < peers.length; i++) {
    var peer = peers[i] || {};
    var peerId = String(peer.id || '').trim();
    if (!peerId) continue;
    html += '<option value="' + _agentPanelAttr(peerId) + '"'
      + (String(draft.peer_id || '') === peerId ? ' selected' : '') + '>'
      + _agentPanelEsc(peer.name || peer.slug || peerId)
      + (peer.group ? ' · ' + _agentPanelEsc(peer.group) : '')
      + '</option>';
  }
  html += '</select></label>';
  html += '<label class="agent-panel-peer-compose-ack">'
    + '<input id="agent-panel-peer-ack-' + _agentPanelAttr(safeId) + '" type="checkbox"'
    + (draft.ack_required ? ' checked' : '')
    + ' onchange="' + _agentPanelEventAttr('agentPanelPeerComposeToggle('
      + architectIdJs + ', this.checked)') + '">'
    + '<span>Ack required</span></label>';
  html += '</div>';
  if (!peers.length) {
    html += '<div class="agent-panel-peer-compose-empty">No same-group peer Architects available.</div>';
  }
  html += '<textarea id="agent-panel-peer-body-' + _agentPanelAttr(safeId) + '"'
    + ' class="agent-panel-peer-compose-body" rows="3"'
    + ' placeholder="Message another Architect…"'
    + ' oninput="' + _agentPanelEventAttr('agentPanelPeerComposeInput('
      + architectIdJs + ', "message", this.value)') + '">'
    + _agentPanelEsc(draft.message || '') + '</textarea>';
  var contextOpen = !!(
    draft.context_task_ids
    || draft.context_engineer_ids
    || draft.context_decision_ids
    || draft.context_summary
  );
  html += '<details class="agent-panel-peer-context"'
    + (contextOpen ? ' open' : '') + '>';
  html += '<summary>Attach context</summary>';
  html += '<div class="agent-panel-peer-context-grid">';
  html += '<label><span>Task IDs</span><input id="agent-panel-peer-tasks-'
    + _agentPanelAttr(safeId) + '" value="' + _agentPanelAttr(draft.context_task_ids || '') + '"'
    + ' placeholder="TORQUE:123, TORQUE:124"'
    + ' oninput="' + _agentPanelEventAttr('agentPanelPeerComposeInput('
      + architectIdJs + ', "context_task_ids", this.value)') + '"></label>';
  html += '<label><span>Engineer IDs</span><input id="agent-panel-peer-engineers-'
    + _agentPanelAttr(safeId) + '" value="' + _agentPanelAttr(draft.context_engineer_ids || '') + '"'
    + ' placeholder="eng-1, eng-2"'
    + ' oninput="' + _agentPanelEventAttr('agentPanelPeerComposeInput('
      + architectIdJs + ', "context_engineer_ids", this.value)') + '"></label>';
  html += '<label><span>Decision IDs</span><input id="agent-panel-peer-decisions-'
    + _agentPanelAttr(safeId) + '" value="' + _agentPanelAttr(draft.context_decision_ids || '') + '"'
    + ' placeholder="decision-1"'
    + ' oninput="' + _agentPanelEventAttr('agentPanelPeerComposeInput('
      + architectIdJs + ', "context_decision_ids", this.value)') + '"></label>';
  html += '<label class="agent-panel-peer-context-summary"><span>Summary</span>'
    + '<textarea id="agent-panel-peer-summary-' + _agentPanelAttr(safeId) + '" rows="2"'
    + ' placeholder="Why this context matters…"'
    + ' oninput="' + _agentPanelEventAttr('agentPanelPeerComposeInput('
      + architectIdJs + ', "context_summary", this.value)') + '">'
    + _agentPanelEsc(draft.context_summary || '') + '</textarea></label>';
  html += '</div></details>';
  html += '<div class="agent-panel-peer-compose-actions">'
    + '<button type="submit" class="engineer-row-btn agent-panel-peer-send">Send peer message</button>'
    + '</div>';
  html += '</form>';
  return html;
}

function _agentPanelInlineThreadMessageList(agent) {
  var agentId = String((agent && agent.id) || '');
  if (!agentId || !state || !state.board_tasks) return [];
  if (typeof _compactHydrateTasksMatching === 'function') {
    _compactHydrateTasksMatching(function(task) {
      if (typeof _compactTaskThreadMayTargetAgent === 'function') {
        return _compactTaskThreadMayTargetAgent(task, agentId);
      }
      var summary = task && task.messages_thread_summary;
      return !!(summary && summary.count && String(task.agent_id || '') === agentId);
    });
  }
  var tasks = state.board_tasks || {};
  var messages = [];
  for (var taskId in tasks) {
    var task = tasks[taskId] || {};
    var thread = Array.isArray(task.messages_thread) ? task.messages_thread : [];
    for (var i = 0; i < thread.length; i++) {
      var entry = thread[i] || {};
      var recipientId = String(entry.recipient_agent_id || '');
      if (recipientId && recipientId !== agentId) continue;
      if (!recipientId && String(task.agent_id || '') !== agentId) continue;
      var ts = Number(entry.timestamp || 0);
      messages.push({
        id: 'inline-thread:' + taskId + ':' + i + ':' + ts,
        action: 'engineer_message',
        message: String(entry.content || ''),
        timestamp: ts,
        sender_id: String(entry.sender_agent_id || ''),
        sender_kind: 'engineer',
        peer_id: String(entry.sender_agent_id || ''),
        direction: 'received',
        task_id: taskId,
        reply_required: !!entry.reply_required,
      });
    }
  }
  messages.sort(function(a, b) {
    var diff = _agentPanelMessageCompareDesc(a, b);
    if (diff) return diff;
    return String(a.id || '').localeCompare(String(b.id || ''));
  });
  return messages;
}

function _agentPanelMcpFilters(agentId) {
  agentId = String(agentId || '');
  if (!_agentPanelMcpFiltersByAgent[agentId]) {
    _agentPanelMcpFiltersByAgent[agentId] = {
      tool: '',
      range: '24h',
      outcome: 'all',
      hook_event_name: _AGENT_PANEL_MCP_DEFAULT_HOOK,
    };
  }
  return _agentPanelMcpFiltersByAgent[agentId];
}

function _agentPanelMcpVisibleLimit(agentId) {
  agentId = String(agentId || '');
  var value = Number(_agentPanelMcpCallsVisibleLimitByAgent[agentId] || 0);
  if (value < _AGENT_PANEL_MCP_PAGE_SIZE) value = _AGENT_PANEL_MCP_PAGE_SIZE;
  _agentPanelMcpCallsVisibleLimitByAgent[agentId] = value;
  return value;
}

function _agentPanelMcpSinceForRange(range) {
  var now = Date.now() / 1000;
  range = String(range || '24h');
  if (range === '1h') return now - 3600;
  if (range === '6h') return now - (6 * 3600);
  if (range === '24h') return now - (24 * 3600);
  return null;
}

function _agentPanelMcpToolPattern(toolText) {
  toolText = String(toolText || '').trim();
  if (!toolText) return 'mcp__torque__%';
  if (toolText.indexOf('%') >= 0 || toolText.indexOf('*') >= 0) return toolText;
  return '*' + toolText + '*';
}

function _agentPanelMcpRequestKey(agentId, filters, limit) {
  return [
    agentId,
    String(filters.tool || ''),
    String(filters.range || ''),
    String(filters.outcome || ''),
    String(filters.hook_event_name || _AGENT_PANEL_MCP_DEFAULT_HOOK),
    String(limit || 0),
  ].join('|');
}

function _agentPanelRequestMcpCalls(agent, options) {
  options = options || {};
  if (!agent || typeof send !== 'function') return;
  var agentId = String(agent.id || '');
  if (!agentId) return;
  if (_agentPanelMcpCallsLoadingByAgent[agentId]) return;
  var filters = _agentPanelMcpFilters(agentId);
  var limit = Math.max(_agentPanelMcpVisibleLimit(agentId), Number(options.limit || 0) || 0);
  var key = _agentPanelMcpRequestKey(agentId, filters, limit);
  var cached = Array.isArray(_agentPanelMcpCallsByAgent[agentId])
    ? _agentPanelMcpCallsByAgent[agentId]
    : null;
  if (!options.force && cached && _agentPanelMcpCallsRequestedKeyByAgent[agentId] === key) {
    return;
  }
  _agentPanelMcpCallsLoadingByAgent[agentId] = true;
  _agentPanelMcpCallsRequestedKeyByAgent[agentId] = key;
  send({
    cmd: 'mcp_calls',
    cell_id: agentId,
    tool_name_pattern: _agentPanelMcpToolPattern(filters.tool),
    hook_event_name: String(filters.hook_event_name || _AGENT_PANEL_MCP_DEFAULT_HOOK),
    since: _agentPanelMcpSinceForRange(filters.range),
    limit: limit,
    success_filter: filters.outcome || 'all',
  });
}

function _agentPanelMcpMergeCalls(current, incoming) {
  var merged = [];
  var seen = {};
  function addAll(items) {
    items = Array.isArray(items) ? items : [];
    for (var i = 0; i < items.length; i++) {
      var call = items[i] || {};
      var key = String(call.cursor || call.idempotency_key || '');
      if (!key) key = String(call.tool_name || '') + '-' + String(call.appended_at || '') + '-' + i;
      if (seen[key]) continue;
      seen[key] = true;
      merged.push(call);
    }
  }
  addAll(incoming);
  addAll(current);
  merged.sort(function(a, b) {
    var at = Number((a && a.appended_at) || 0);
    var bt = Number((b && b.appended_at) || 0);
    if (at !== bt) return bt - at;
    return Number((b && b.cursor) || 0) - Number((a && a.cursor) || 0);
  });
  if (merged.length > 500) merged.length = 500;
  return merged;
}

function agentPanelReceiveMcpCalls(data) {
  var agentId = String((data && (data.cell_id || data.agent_id)) || '').trim();
  if (!agentId) return;
  var calls = Array.isArray(data.calls) ? data.calls : (Array.isArray(data.events) ? data.events : []);
  _agentPanelMcpCallsByAgent[agentId] = calls.slice();
  _agentPanelMcpCallsLoadingByAgent[agentId] = false;
  if (!state.mcp_calls) state.mcp_calls = {};
  state.mcp_calls[agentId] = calls.slice();
  var focused = _resolveFocusedAgent();
  if (focused && String(focused.id || '') === agentId
      && _agentPanelIsMcpSubtabActive(focused)) {
    if (typeof _agentPanelRefreshCurrentTab === 'function'
        && _agentPanelRefreshCurrentTab()) return;
    if (typeof renderAgentPanel === 'function') renderAgentPanel();
  }
}

function agentPanelReceiveMcpCallAppend(call) {
  call = call || {};
  var agentId = String(call.cell_id || '');
  if (!agentId) return;
  if (!_agentPanelMcpCallMatchesHook(call, _agentPanelMcpFilters(agentId))) return;
  var current = _agentPanelMcpCallsByAgent[agentId]
    || (state && state.mcp_calls && state.mcp_calls[agentId])
    || [];
  _agentPanelMcpCallsByAgent[agentId] = _agentPanelMcpMergeCalls(current, [call]);
  if (!state.mcp_calls) state.mcp_calls = {};
  state.mcp_calls[agentId] = _agentPanelMcpCallsByAgent[agentId].slice();
  var focused = _resolveFocusedAgent();
  if (focused && String(focused.id || '') === agentId
      && _agentPanelIsMcpSubtabActive(focused)) {
    if (typeof _agentPanelRefreshCurrentTab === 'function'
        && _agentPanelRefreshCurrentTab()) return;
    if (typeof renderAgentPanel === 'function') renderAgentPanel();
  }
}

function agentPanelMcpFilterChange(field, value) {
  var agent = _resolveFocusedAgent();
  if (!agent) return;
  var agentId = String(agent.id || '');
  var filters = _agentPanelMcpFilters(agentId);
  if (field === 'tool') filters.tool = String(value || '');
  else if (field === 'range') filters.range = String(value || '24h');
  else if (field === 'outcome') filters.outcome = String(value || 'all');
  _agentPanelMcpCallsVisibleLimitByAgent[agentId] = _AGENT_PANEL_MCP_PAGE_SIZE;
  _agentPanelMcpCallsRequestedKeyByAgent[agentId] = '';
  _agentPanelRequestMcpCalls(agent, { force: true });
  if (typeof _agentPanelRefreshCurrentTab === 'function'
      && _agentPanelRefreshCurrentTab()) return;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function agentPanelLoadOlderMcpCalls(evt, agentId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  agentId = String(agentId || '').trim();
  var agent = (state && state.agents && agentId) ? state.agents[agentId] : null;
  if (!agent) agent = _resolveFocusedAgent();
  if (!agent) return;
  agentId = String(agent.id || '');
  _agentPanelMcpCallsVisibleLimitByAgent[agentId] =
    _agentPanelMcpVisibleLimit(agentId) + _AGENT_PANEL_MCP_PAGE_SIZE;
  _agentPanelRequestMcpCalls(agent, { force: true });
  if (typeof _agentPanelRefreshCurrentTab === 'function'
      && _agentPanelRefreshCurrentTab()) return;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function agentPanelToggleMcpCall(agentId, cursor) {
  agentId = String(agentId || '').trim();
  cursor = String(cursor || '').trim();
  if (!agentId || !cursor) return;
  if (!_agentPanelMcpCallExpandedByAgent[agentId]) {
    _agentPanelMcpCallExpandedByAgent[agentId] = {};
  }
  _agentPanelMcpCallExpandedByAgent[agentId][cursor] =
    !_agentPanelMcpCallExpandedByAgent[agentId][cursor];
  if (typeof _agentPanelRefreshCurrentTab === 'function'
      && _agentPanelRefreshCurrentTab()) return;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function _agentPanelMcpCallsForAgent(agent) {
  var agentId = String((agent && agent.id) || '');
  if (!agentId) return [];
  if (Array.isArray(_agentPanelMcpCallsByAgent[agentId])) {
    return _agentPanelMcpCallsByAgent[agentId].slice();
  }
  if (state && state.mcp_calls && Array.isArray(state.mcp_calls[agentId])) {
    return state.mcp_calls[agentId].slice();
  }
  return [];
}

function _agentPanelMcpHookFilter(filters) {
  filters = filters || {};
  return String(filters.hook_event_name || _AGENT_PANEL_MCP_DEFAULT_HOOK || '').trim();
}

function _agentPanelMcpCallMatchesHook(call, filters) {
  var hookFilter = _agentPanelMcpHookFilter(filters);
  if (!hookFilter) return true;
  return String((call && call.hook_event_name) || '') === hookFilter;
}

function _agentPanelMcpCallMatchesFilters(call, filters) {
  filters = filters || {};
  if (!_agentPanelMcpCallMatchesHook(call, filters)) return false;
  var outcome = String(filters.outcome || 'all');
  if (outcome === 'success' && !call.success) return false;
  if (outcome === 'error' && call.success) return false;
  var toolText = String(filters.tool || '').trim().toLowerCase();
  if (toolText) {
    var tool = String(call.tool_name || '').toLowerCase();
    if (tool.indexOf(toolText.replace(/\*/g, '').replace(/%/g, '')) < 0) {
      return false;
    }
  }
  var since = _agentPanelMcpSinceForRange(filters.range);
  if (since && Number(call.appended_at || 0) < since) return false;
  return true;
}

function _agentPanelMcpSummary(value) {
  if (value == null) return 'redacted';
  if (typeof value === 'object' && value.redacted) {
    var keys = Array.isArray(value.arg_keys) ? value.arg_keys : [];
    if (keys.length) return 'redacted keys: ' + keys.join(', ');
    return 'redacted · ' + (value.byte_size || 0) + ' bytes';
  }
  if (typeof value === 'object') {
    try {
      var json = JSON.stringify(value);
      return json.length > 120 ? json.slice(0, 117) + '…' : json;
    } catch (err) {
      return '[object]';
    }
  }
  var text = String(value);
  return text.length > 120 ? text.slice(0, 117) + '…' : text;
}

function _agentPanelMcpPretty(value) {
  if (value == null) return 'redacted';
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value, null, 2);
    } catch (err) {
      return String(value);
    }
  }
  return String(value);
}

function _agentPanelRenderMcpCall(call, agentId) {
  call = call || {};
  var cursor = String(call.cursor || call.idempotency_key || '');
  var expanded = !!(
    _agentPanelMcpCallExpandedByAgent[agentId]
    && _agentPanelMcpCallExpandedByAgent[agentId][cursor]
  );
  var status = call.success ? 'success' : 'error';
  var duration = call.duration_ms != null ? (' · ' + call.duration_ms + 'ms') : '';
  var argsRedacted = !!call.args_redacted;
  var resultRedacted = !!call.result_redacted;
  var html = '<div class="agent-panel-mcp-call agent-panel-mcp-' + status + '"'
    + ' data-agent-panel-anchor="mcp-call-' + _agentPanelAttr(cursor) + '">';
  html += '<button type="button" class="agent-panel-mcp-row" onclick="agentPanelToggleMcpCall(\''
    + _agentPanelAttr(agentId) + '\', \'' + _agentPanelAttr(cursor) + '\')">';
  html += '<span class="agent-panel-mcp-status">' + (call.success ? '✓' : '!') + '</span>';
  html += '<span class="agent-panel-mcp-main">';
  html += '<span class="agent-panel-mcp-tool">' + _agentPanelEsc(call.tool_name || 'unknown tool') + '</span>';
  html += '<span class="agent-panel-mcp-args">' + _agentPanelEsc(_agentPanelMcpSummary(call.args)) + '</span>';
  html += '</span>';
  html += '<span class="agent-panel-mcp-meta">' + _agentPanelEsc(_agentPanelTimeAgo(call.appended_at))
    + _agentPanelEsc(duration) + '</span>';
  html += '</button>';
  if (expanded) {
    html += '<div class="agent-panel-mcp-detail">';
    html += '<div class="agent-panel-mcp-detail-grid">';
    html += '<div><div class="agent-panel-mcp-detail-label">Args</div>';
    if (argsRedacted) html += '<div class="agent-panel-mcp-redacted">Args redacted at ingest.</div>';
    html += '<pre>' + _agentPanelEsc(_agentPanelMcpPretty(call.args)) + '</pre></div>';
    html += '<div><div class="agent-panel-mcp-detail-label">Result</div>';
    if (resultRedacted) html += '<div class="agent-panel-mcp-redacted">Result redacted at ingest.</div>';
    html += '<pre>' + _agentPanelEsc(_agentPanelMcpPretty(call.result)) + '</pre></div>';
    html += '</div>';
    if (call.error) {
      html += '<div class="agent-panel-mcp-error-text">' + _agentPanelEsc(call.error) + '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _renderAgentMcpTab(agent) {
  _agentPanelRequestMcpCalls(agent);
  var agentId = String((agent && agent.id) || '');
  var filters = _agentPanelMcpFilters(agentId);
  var calls = _agentPanelMcpCallsForAgent(agent).filter(function(call) {
    return _agentPanelMcpCallMatchesFilters(call, filters);
  });
  var visibleLimit = _agentPanelMcpVisibleLimit(agentId);
  var visibleCalls = calls.slice(0, visibleLimit);
  var loading = !!_agentPanelMcpCallsLoadingByAgent[agentId];
  var html = '<div class="agent-panel-mcp-tab">';
  html += '<div class="agent-panel-mcp-filters">';
  html += '<input class="agent-panel-mcp-filter-tool" placeholder="Filter tool" value="'
    + _agentPanelAttr(filters.tool || '')
    + '" oninput="agentPanelMcpFilterChange(\'tool\', this.value)">';
  html += '<select onchange="agentPanelMcpFilterChange(\'range\', this.value)">';
  [
    ['1h', 'Last hour'],
    ['6h', '6h'],
    ['24h', '24h'],
    ['all', 'All'],
  ].forEach(function(opt) {
    html += '<option value="' + opt[0] + '"' + (filters.range === opt[0] ? ' selected' : '')
      + '>' + opt[1] + '</option>';
  });
  html += '</select>';
  html += '<select onchange="agentPanelMcpFilterChange(\'outcome\', this.value)">';
  [
    ['all', 'All outcomes'],
    ['success', 'Success only'],
    ['error', 'Errors only'],
  ].forEach(function(opt) {
    html += '<option value="' + opt[0] + '"' + (filters.outcome === opt[0] ? ' selected' : '')
      + '>' + opt[1] + '</option>';
  });
  html += '</select>';
  html += '</div>';
  if (loading && !calls.length) {
    html += '<div class="agent-panel-event-empty">Loading MCP calls…</div>';
  } else if (!calls.length) {
    html += '<div class="agent-panel-event-empty">No MCP calls found.</div>';
  } else {
    if (loading) html += '<div class="agent-panel-worklog-note">Refreshing MCP calls…</div>';
    html += '<div class="agent-panel-mcp-list">';
    for (var i = 0; i < visibleCalls.length; i++) {
      html += _agentPanelRenderMcpCall(visibleCalls[i], agentId);
    }
    html += '</div>';
    if (calls.length >= visibleLimit) {
      html += '<button type="button" class="agent-panel-event-load-more" onclick="agentPanelLoadOlderMcpCalls(event, \''
        + _agentPanelAttr(agentId) + '\')">Load more</button>';
    }
  }
  html += '</div>';
  return html;
}

function _renderEngineerJournal(agent) {
  var group = String((agent && agent.group) || '');
  if (typeof _agentPanelLegacyRenderJournal === 'function') return _agentPanelLegacyRenderJournal(group, agent);
  var entries = _agentPanelEngineerJournalEntries(group, agent);
  if (typeof _agentPanelLegacyRenderJournalEntries === 'function') return _agentPanelLegacyRenderJournalEntries(entries, true);
  if (!entries.length) return '<div class="agent-panel-empty">No journal entries yet.</div>';
  var html = '<div class="agent-panel-journal">';
  for (var i = 0; i < entries.length; i++) {
    var entry = entries[i] || {};
    html += '<div class="agent-panel-entry">';
    html += '<div class="agent-panel-entry-header">';
    html += '<span class="agent-panel-badge">' + _agentPanelEsc(entry.type || 'note') + '</span>';
    html += '<span class="agent-panel-entry-time">' + _agentPanelEsc(_agentPanelTimeAgo(entry.timestamp)) + '</span>';
    html += '</div>';
    html += '<div class="agent-panel-entry-text">' + _agentPanelEsc(entry.entry || '') + '</div>';
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelEngineerJournalAuthorId(group, agent) {
  if (agent && String((agent.kind || '')).trim() === 'engineer'
      && String(agent.id || '').trim()) {
    return String(agent.id || '').trim();
  }
  var configured = group ? _agentPanelEngineerAgent(group) : null;
  return configured && configured.id ? String(configured.id || '').trim() : '';
}

function _agentPanelEngineerJournalEntries(group, agent) {
  var store = state && state.engineer_journal ? state.engineer_journal : null;
  if (!store) return [];
  var authorId = _agentPanelEngineerJournalAuthorId(group, agent);
  if (authorId && Array.isArray(store[authorId])) return store[authorId];
  var legacy = group && Array.isArray(store[group]) ? store[group] : [];
  if (authorId && legacy.length) {
    return legacy.filter(function(entry) {
      return String((entry && entry.author_cell_id) || '') === authorId;
    });
  }
  return [];
}

function _renderAgentDigestEvents(agent) {
  var group = String((agent && agent.group) || '');
  var bstats = _agentPanelDigestBufferStats(agent);
  var sentEvents = _agentPanelDigestSentEvents(agent);
  var settings = _agentPanelDigestSettings(agent);
  var paused = !!(settings && settings.paused);
  if (!group) return '<div class="agent-panel-empty">No group events yet.</div>';
  return _agentPanelRenderEventsTab(
    bstats,
    sentEvents,
    paused,
    agent,
    'agentPanelSendNow(\'' + _agentPanelEsc((agent && agent.id) || '') + '\')',
    'Already digested to ' + _agentPanelEsc((agent && (agent.name || agent.id)) || 'engineer')
  );
}

function _agentPanelUsesMergedCellEvents(agent) {
  var kind = _agentPanelKind(agent);
  return kind === 'architect' || kind === 'engineer';
}

function _agentPanelFallbackPanelEvents(agent) {
  var agentId = String((agent && agent.id) || '');
  if (!agentId || !state || !Array.isArray(state.panel_events)) return [];
  return state.panel_events.filter(function(evt) {
    return String((evt && evt.cell_id) || '') === agentId;
  });
}

function _agentPanelRequestCellEvents(agent) {
  if (!_agentPanelUsesMergedCellEvents(agent)) return;
  if (typeof send !== 'function') return;
  var agentId = String((agent && agent.id) || '');
  if (!agentId) return;
  if (_agentPanelCellEventsLoadingById[agentId]) return;
  var now = Date.now();
  var lastFetch = Number(_agentPanelCellEventsLastFetchById[agentId] || 0);
  var eventAt = Number((agent && agent.last_event_at) || 0);
  var lastFetchEventAt = Number(_agentPanelCellEventsLastFetchEventAtById[agentId] || 0);
  if (lastFetch && eventAt === lastFetchEventAt
      && now - lastFetch < _AGENT_PANEL_CELL_EVENTS_REFRESH_MS) return;
  _agentPanelCellEventsLoadingById[agentId] = true;
  _agentPanelCellEventsLastFetchById[agentId] = now;
  _agentPanelCellEventsLastFetchEventAtById[agentId] = eventAt;
  send({ cmd: 'get_cell_events', cell_id: agentId, limit: 200 });
}

function agentPanelReceiveCellEvents(data) {
  var agentId = String((data && data.cell_id) || '');
  if (!agentId) return;
  _agentPanelCellEventsById[agentId] = Array.isArray(data.events)
    ? data.events.slice()
    : [];
  _agentPanelCellEventsLoadingById[agentId] = false;
  _agentPanelCellEventsLastFetchById[agentId] = Date.now();
  var focused = _resolveFocusedAgent();
  if (focused && String(focused.id || '') === agentId
      && _agentPanelActiveTab(_agentPanelKind(focused)) === 'events') {
    renderAgentPanel();
  }
}

function _agentPanelCellEventsForAgent(agent) {
  var agentId = String((agent && agent.id) || '');
  var cached = _agentPanelCellEventsById[agentId];
  if (Array.isArray(cached)) return cached.slice();
  return _agentPanelFallbackPanelEvents(agent);
}

function _agentPanelRenderCellEventItem(evt, index) {
  evt = evt || {};
  var rawId = evt.id || ('idx-' + index);
  var anchorKey = 'cell-event-' + String(rawId);
  var kind = typeof _engineerEventKindLabel === 'function'
    ? _engineerEventKindLabel(evt.kind)
    : String(evt.kind || 'event').replace(/_/g, ' ');
  var summary = evt.message || kind;
  var source = String(evt.source || '');
  var meta = _agentPanelTimeAgo(evt.timestamp);
  if (source === 'event_log') meta += meta ? ' · live' : 'live';
  else if (source === 'panel_events') meta += meta ? ' · persisted' : 'persisted';

  var html = '<div class="agent-panel-event-item agent-panel-event-item-sent" data-agent-panel-anchor="'
    + _agentPanelEsc(anchorKey) + '">';
  html += '<div class="agent-panel-event-item-header">';
  html += '<span class="agent-panel-event-kind">' + _agentPanelEsc(kind) + '</span>';
  html += '<span class="agent-panel-event-meta">' + _agentPanelEsc(meta) + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-event-message">' + _agentPanelEsc(summary) + '</div>';
  if (evt.task_id) {
    html += '<div class="agent-panel-event-task">' + _agentPanelEsc(evt.task_id) + '</div>';
  }
  html += '</div>';
  return html;
}

function _renderPersistentCellEvents(agent) {
  _agentPanelRequestCellEvents(agent);
  var agentId = String((agent && agent.id) || '');
  var events = _agentPanelSortedCellEvents(agent);
  var page = _agentPanelEventPage(agent, events);
  var loading = !!_agentPanelCellEventsLoadingById[agentId];

  var html = '<div class="agent-panel-event-section">';
  html += '<div class="agent-panel-event-section-header">';
  html += '<span class="agent-panel-event-section-title">Cell events</span>';
  html += '<span class="agent-panel-event-section-count">' + _agentPanelEventSectionCount(page) + '</span>';
  html += '</div>';
  if (loading && !events.length) {
    html += '<div class="agent-panel-event-empty">Loading cell events…</div>';
    html += '</div>';
    return html;
  }
  if (!events.length) {
    html += '<div class="agent-panel-event-empty">No cell events yet.</div>';
    html += '</div>';
    return html;
  }
  if (loading) {
    html += '<div class="agent-panel-worklog-note">Refreshing cell events…</div>';
  }
  html += '<div class="agent-panel-event-list">';
  for (var i = 0; i < page.events.length; i++) {
    html += _agentPanelRenderCellEventItem(page.events[i], i);
  }
  html += '</div>';
  html += _agentPanelRenderEventLoadMore(page);
  html += '</div>';
  return html;
}

function _renderEngineerEvents(agent) {
  return _renderAgentEventsWithInnerTabs(agent);
}

function _renderArchitectEvents(agent) {
  return _renderAgentEventsWithInnerTabs(agent);
}

function _agentPanelQueuedLaneOrder(lane) {
  lane = String(lane || '').trim();
  if (lane === 'Backlog') return 0;
  if (lane === 'To Do') return 1;
  if (lane === 'In Progress') return 2;
  return 99;
}

function _agentPanelTaskTimestampSeconds(task) {
  task = task || {};
  var raw = task.lane_entered_at || task.updated_at || task.started_at || task.created_at || 0;
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : 0;
  if (raw == null || raw === '') return 0;
  var numeric = Number(raw);
  if (Number.isFinite(numeric) && String(raw).trim() !== '') return numeric;
  var parsed = Date.parse(String(raw));
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : 0;
}

function _agentPanelTaskPosition(task) {
  var pos = Number(task && task.position);
  return Number.isFinite(pos) ? pos : 0;
}

function _agentPanelEngineerQueuedTaskEntries(agent) {
  var engineerId = String((agent && agent.id) || '').trim();
  var tasks = [];
  if (!engineerId || !state || !state.board_tasks) return tasks;
  for (var taskId in state.board_tasks) {
    var task = state.board_tasks[taskId];
    if (!task) continue;
    if (typeof taskIsEngineerMessageFollowup === 'function'
        && taskIsEngineerMessageFollowup(task)) continue;
    if (String(task.assigned_engineer_id || '').trim() !== engineerId) continue;
    var lane = String(task.lane || '').trim();
    if (lane !== 'Backlog' && lane !== 'To Do' && lane !== 'In Progress') continue;
    tasks.push(task);
  }
  tasks.sort(function(a, b) {
    var laneDiff = _agentPanelQueuedLaneOrder(a && a.lane)
      - _agentPanelQueuedLaneOrder(b && b.lane);
    if (laneDiff) return laneDiff;
    var posDiff = _agentPanelTaskPosition(a) - _agentPanelTaskPosition(b);
    if (posDiff) return posDiff;
    var tsDiff = _agentPanelTaskTimestampSeconds(b) - _agentPanelTaskTimestampSeconds(a);
    if (tsDiff) return tsDiff;
    return String((a && a.id) || '').localeCompare(String((b && b.id) || ''));
  });
  return tasks;
}

function _agentPanelTaskWorkerLabel(task) {
  var workerId = String((task && task.agent_id) || '').trim();
  if (!workerId || !state || !state.agents || !state.agents[workerId]) return '';
  var worker = state.agents[workerId];
  return worker.name || worker.slug || worker.id || '';
}

function _agentPanelRenderEngineerQueuedTaskItem(agent, task) {
  var taskId = (task && task.id) || '';
  var title = (task && (task.task || task.title)) || taskId || 'Task';
  var lane = (task && task.lane) || 'Queued';
  var status = task ? String(task.status || '').trim() : '';
  var engineerName = _agentPanelAgentDisplayName(agent, 'Engineer');
  var workerLabel = _agentPanelTaskWorkerLabel(task);
  var ts = _agentPanelTaskTimestampSeconds(task);
  var meta = ts ? ('updated ' + _agentPanelTimeAgo(ts)) : 'queued task';
  if (workerLabel) meta = 'worker ' + workerLabel + ' · ' + meta;
  var anchorKey = 'engineer-queued-task-' + String(taskId || title);

  var html = '<div class="agent-panel-worklog-item agent-panel-queued-task" data-agent-panel-anchor="'
    + _agentPanelEsc(anchorKey) + '">';
  html += '<div class="agent-panel-worklog-item-header">';
  html += '<div class="agent-panel-worklog-task">';
  html += '<div class="agent-panel-worklog-task-title">' + _agentPanelEsc(title) + '</div>';
  if (taskId) {
    html += '<div class="agent-panel-worklog-task-id">' + _agentPanelEsc(taskId) + '</div>';
  }
  html += '</div>';
  html += '<div class="agent-panel-worklog-lane">' + _agentPanelEsc(lane) + '</div>';
  html += '</div>';
  html += '<div class="agent-panel-worklog-meta-row">';
  html += '<span class="agent-panel-worklog-agent">' + _agentPanelEsc(engineerName) + '</span>';
  html += '<span class="agent-panel-worklog-meta">' + _agentPanelEsc(meta) + '</span>';
  html += '</div>';
  if (status) {
    html += '<div class="agent-panel-worklog-status">' + _agentPanelEsc(status) + '</div>';
  }
  html += '</div>';
  return html;
}

function _renderEngineerQueuedTasks(agent) {
  var tasks = _agentPanelEngineerQueuedTaskEntries(agent);
  var agentName = _agentPanelAgentDisplayName(agent, 'this engineer');
  var html = '<div class="agent-panel-worklog-tab agent-panel-queued-tab">';
  html += '<div class="agent-panel-worklog-header">';
  html += '<span class="agent-panel-worklog-title">Queued tasks</span>';
  html += '<span class="agent-panel-worklog-count">' + tasks.length + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-worklog-note">Tasks assigned to '
    + _agentPanelEsc(agentName)
    + ' in Backlog, To Do, or In Progress.</div>';
  if (!tasks.length) {
    html += '<div class="agent-panel-event-empty">No queued tasks for this engineer.</div>';
    html += '</div>';
    return html;
  }
  html += '<div class="agent-panel-worklog-list">';
  for (var i = 0; i < tasks.length; i++) {
    html += _agentPanelRenderEngineerQueuedTaskItem(agent, tasks[i]);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _renderEngineerWorklog(agent) {
  var group = String((agent && agent.group) || '');
  var ws = _agentPanelEngineerSettings(group);
  if (typeof _agentPanelLegacyRenderWorklog === 'function') return _agentPanelLegacyRenderWorklog(group, ws);
  return '<div class="agent-panel-empty">No completed tasks yet.</div>';
}

function _agentPanelSpecializationsListMatchesGroup(group) {
  if (typeof _orderedSpecializationsListMatchesGroup === 'function') {
    return _orderedSpecializationsListMatchesGroup(group);
  }
  return String((state && state.specializations_group) || '') === String(group || '');
}

function _agentPanelSpecializationCatalog(group) {
  if (typeof _orderedSpecializationsCatalog === 'function') {
    return _orderedSpecializationsCatalog(group, { projectOnly: true });
  }
  var matchesGroup = _agentPanelSpecializationsListMatchesGroup(group);
  var raw = matchesGroup && state && Array.isArray(state.specializations)
    ? state.specializations
    : [];
  var names = [];
  var metaByName = {};
  var seen = {};
  for (var i = 0; i < raw.length; i++) {
    var item = raw[i] || {};
    if (item.global) continue;
    var name = String(item.name || '').trim();
    if (!name || seen[name]) continue;
    seen[name] = true;
    names.push(name);
    metaByName[name] = item;
  }
  return { matchesGroup: matchesGroup, names: names, metaByName: metaByName };
}

function _agentPanelNormalizeSpecializations(raw, availableNames, filterKnown) {
  if (typeof _normalizeOrderedSpecializationSelection === 'function') {
    return _normalizeOrderedSpecializationSelection(raw, availableNames, {
      filterKnown: !!filterKnown,
    });
  }
  var available = Array.isArray(availableNames) ? availableNames : [];
  var availableSet = available.length ? {} : null;
  if (availableSet) {
    for (var i = 0; i < available.length; i++) availableSet[available[i]] = true;
  }
  var out = [];
  var seen = {};
  var items = Array.isArray(raw) ? raw : [];
  for (var j = 0; j < items.length; j++) {
    var name = String(items[j] || '').trim();
    if (!name || seen[name]) continue;
    if (filterKnown && availableSet && !availableSet[name]) continue;
    seen[name] = true;
    out.push(name);
  }
  return out;
}

function _agentPanelEnsureSpecializationsList(group) {
  group = String(group || '');
  if (!group || _agentPanelSpecializationsListMatchesGroup(group)) return;
  var now = Date.now ? Date.now() : 0;
  if (_agentPanelSpecializationsRequestedGroup === group
      && now - Number(_agentPanelSpecializationsRequestedAt || 0) < 1000) {
    return;
  }
  _agentPanelSpecializationsRequestedGroup = group;
  _agentPanelSpecializationsRequestedAt = now;
  if (typeof send === 'function') send({ cmd: 'list_specializations', group: group });
}

function _agentPanelEngineerSpecializationsCurrent(agent) {
  var raw = agent && Array.isArray(agent.engineer_specializations)
    ? agent.engineer_specializations
    : [];
  return raw.map(function(item) {
    return String(item || '').trim();
  }).filter(Boolean);
}

function _agentPanelEngineerSpecializationsArchitectId(agent) {
  if (!agent || _agentPanelKind(agent) !== 'engineer') return '';
  return String(agent.hired_by_architect_id || '').trim();
}

function _agentPanelEngineerSpecializationState(engineerId) {
  engineerId = String(engineerId || '').trim();
  if (!_agentPanelEngineerSpecializationEditors[engineerId]) {
    _agentPanelEngineerSpecializationEditors[engineerId] = {
      editing: false,
      draft: [],
      error: '',
      saving: false,
    };
  }
  return _agentPanelEngineerSpecializationEditors[engineerId];
}

function _agentPanelEngineerSpecializationSelection(agent, ui, catalog) {
  var raw = ui && ui.editing ? ui.draft : _agentPanelEngineerSpecializationsCurrent(agent);
  return _agentPanelNormalizeSpecializations(
    raw,
    catalog.names,
    catalog.matchesGroup
  );
}

function _agentPanelSpecializationChipsHtml(names) {
  names = Array.isArray(names) ? names : [];
  if (!names.length) {
    return '<span class="agent-panel-specialization-empty">Generalist (no specialization)</span>';
  }
  var html = '<div class="agent-panel-specialization-chips">';
  for (var i = 0; i < names.length; i++) {
    html += '<span class="agent-panel-specialization-chip'
      + (i === 0 ? ' primary' : '')
      + '">' + _agentPanelEsc(names[i])
      + (i === 0 ? ' <span class="agent-panel-specialization-primary">(primary)</span>' : '')
      + '</span>';
  }
  html += '</div>';
  return html;
}

function _agentPanelEngineerSpecializationsEditorHtml(agent) {
  var engineerId = String((agent && agent.id) || '').trim();
  var architectId = _agentPanelEngineerSpecializationsArchitectId(agent);
  if (!engineerId || !architectId) return '';
  var group = String((agent && agent.group) || '');
  _agentPanelEnsureSpecializationsList(group);
  var architect = state && state.agents ? state.agents[architectId] : null;
  var architectLabel = architect
    ? (architect.name || architect.slug || architect.id)
    : architectId;
  var catalog = _agentPanelSpecializationCatalog(group);
  var ui = _agentPanelEngineerSpecializationState(engineerId);
  var selected = _agentPanelEngineerSpecializationSelection(agent, ui, catalog);
  if (ui.editing) ui.draft = selected.slice();
  var engineerIdJs = JSON.stringify(engineerId);
  var safeId = _agentPanelDomIdToken(engineerId);
  var html = '<div class="detail-section-card agent-panel-specializations-card"'
    + ' data-agent-panel-specializations-editor="' + _agentPanelAttr(engineerId) + '">';
  html += '<div class="detail-section-card-head">';
  html += '<div><div class="detail-section-primary">Specializations</div>';
  html += '<div class="agent-panel-worklog-note">Architect-owned routing metadata for '
    + _agentPanelEsc(architectLabel)
    + '. First selected is primary.</div></div>';
  html += '<div class="detail-section-card-actions">';
  if (ui.editing) {
    html += '<button type="button" class="detail-inline-editor-btn detail-inline-editor-btn-primary"'
      + (ui.saving ? ' disabled' : '')
      + ' onclick="' + _agentPanelEventAttr('agentPanelSaveEngineerSpecializations(' + engineerIdJs + ')') + '">'
      + (ui.saving ? 'Saving…' : 'Save')
      + '</button>';
    html += '<button type="button" class="detail-inline-editor-btn" onclick="'
      + _agentPanelEventAttr('agentPanelCancelEngineerSpecializationsEdit(' + engineerIdJs + ')')
      + '">Cancel</button>';
  } else {
    html += '<button type="button" class="detail-inline-editor-btn" onclick="'
      + _agentPanelEventAttr('agentPanelStartEngineerSpecializationsEdit(' + engineerIdJs + ')')
      + '">Edit</button>';
  }
  html += '</div></div>';
  if (ui.error) {
    html += '<div class="agent-panel-specializations-error">'
      + _agentPanelEsc(ui.error)
      + '</div>';
  }
  if (!catalog.matchesGroup) {
    html += '<div class="agent-panel-worklog-note">Loading project specialization options…</div>';
  }
  if (!ui.editing) {
    html += _agentPanelSpecializationChipsHtml(selected);
    html += '</div>';
    return html;
  }
  html += '<div class="specializations-picker agent-panel-specializations-picker">';
  html += '<ul class="specializations-selected">';
  for (var i = 0; i < selected.length; i++) {
    html += '<li class="specialization-entry">';
    html += '<span class="specialization-entry-label">'
      + _agentPanelEsc(selected[i])
      + (i === 0 ? ' (primary)' : '')
      + '</span>';
    html += '<span class="specialization-controls-row">';
    if (i > 0) {
      html += '<button type="button" title="Move up" onclick="'
        + _agentPanelEventAttr('agentPanelMoveEngineerSpecialization(' + engineerIdJs + ',' + i + ',-1)')
        + '">↑</button>';
    }
    if (i < selected.length - 1) {
      html += '<button type="button" title="Move down" onclick="'
        + _agentPanelEventAttr('agentPanelMoveEngineerSpecialization(' + engineerIdJs + ',' + i + ',1)')
        + '">↓</button>';
    }
    html += '<button type="button" title="Remove" onclick="'
      + _agentPanelEventAttr('agentPanelRemoveEngineerSpecialization(' + engineerIdJs + ',' + i + ')')
      + '">×</button>';
    html += '</span></li>';
  }
  html += '</ul>';
  html += '<div class="specializations-controls">';
  html += '<select id="agent-panel-specializations-available-' + _agentPanelAttr(safeId) + '">';
  html += '<option value="">'
    + _agentPanelEsc(!catalog.matchesGroup
      ? 'Loading specializations...'
      : (catalog.names.length ? 'Pick a specialization...' : 'No specializations available'))
    + '</option>';
  for (var optionIndex = 0; optionIndex < catalog.names.length; optionIndex++) {
    var name = catalog.names[optionIndex];
    if (selected.indexOf(name) >= 0) continue;
    var meta = catalog.metaByName[name] || {};
    html += '<option value="' + _agentPanelAttr(name) + '"'
      + (meta.preamble ? ' title="' + _agentPanelAttr(String(meta.preamble).slice(0, 200)) + '"' : '')
      + '>' + _agentPanelEsc(name) + '</option>';
  }
  html += '</select>';
  html += '<button type="button" class="btn-secondary" onclick="'
    + _agentPanelEventAttr('agentPanelAddEngineerSpecialization(' + engineerIdJs + ')')
    + '">+ Add</button>';
  html += '</div></div></div>';
  return html;
}

function _agentPanelRefreshAfterSpecializationEdit() {
  if (typeof _agentPanelRefreshCurrentTab === 'function'
      && _agentPanelRefreshCurrentTab()) {
    return;
  }
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function agentPanelStartEngineerSpecializationsEdit(engineerId) {
  var agent = state && state.agents ? state.agents[String(engineerId || '')] : null;
  if (!agent) return;
  var ui = _agentPanelEngineerSpecializationState(engineerId);
  var catalog = _agentPanelSpecializationCatalog(agent.group || '');
  ui.editing = true;
  ui.saving = false;
  ui.error = '';
  ui.draft = _agentPanelNormalizeSpecializations(
    _agentPanelEngineerSpecializationsCurrent(agent),
    catalog.names,
    catalog.matchesGroup
  );
  _agentPanelEnsureSpecializationsList(agent.group || '');
  _agentPanelRefreshAfterSpecializationEdit();
}

function agentPanelCancelEngineerSpecializationsEdit(engineerId) {
  var ui = _agentPanelEngineerSpecializationState(engineerId);
  ui.editing = false;
  ui.saving = false;
  ui.error = '';
  ui.draft = [];
  _agentPanelRefreshAfterSpecializationEdit();
}

function agentPanelAddEngineerSpecialization(engineerId, name) {
  var agent = state && state.agents ? state.agents[String(engineerId || '')] : null;
  if (!agent) return;
  var safeId = _agentPanelDomIdToken(engineerId);
  if (typeof name === 'undefined') {
    var select = document.getElementById('agent-panel-specializations-available-' + safeId);
    name = select ? select.value : '';
  }
  name = String(name || '').trim();
  if (!name) return;
  var catalog = _agentPanelSpecializationCatalog(agent.group || '');
  if (catalog.matchesGroup && catalog.names.indexOf(name) < 0) return;
  var ui = _agentPanelEngineerSpecializationState(engineerId);
  ui.editing = true;
  ui.error = '';
  if (!Array.isArray(ui.draft)) ui.draft = [];
  if (ui.draft.indexOf(name) < 0) ui.draft.push(name);
  ui.draft = _agentPanelNormalizeSpecializations(
    ui.draft,
    catalog.names,
    catalog.matchesGroup
  );
  _agentPanelRefreshAfterSpecializationEdit();
}

function agentPanelRemoveEngineerSpecialization(engineerId, idx) {
  var ui = _agentPanelEngineerSpecializationState(engineerId);
  if (!Array.isArray(ui.draft)) ui.draft = [];
  if (idx < 0 || idx >= ui.draft.length) return;
  ui.editing = true;
  ui.error = '';
  ui.draft.splice(idx, 1);
  _agentPanelRefreshAfterSpecializationEdit();
}

function agentPanelMoveEngineerSpecialization(engineerId, idx, delta) {
  var ui = _agentPanelEngineerSpecializationState(engineerId);
  if (!Array.isArray(ui.draft)) ui.draft = [];
  var newIdx = idx + delta;
  if (newIdx < 0 || newIdx >= ui.draft.length) return;
  ui.editing = true;
  ui.error = '';
  var moved = ui.draft.splice(idx, 1)[0];
  ui.draft.splice(newIdx, 0, moved);
  _agentPanelRefreshAfterSpecializationEdit();
}

function agentPanelSaveEngineerSpecializations(engineerId) {
  engineerId = String(engineerId || '').trim();
  var agent = state && state.agents ? state.agents[engineerId] : null;
  if (!agent) return;
  var architectId = _agentPanelEngineerSpecializationsArchitectId(agent);
  var ui = _agentPanelEngineerSpecializationState(engineerId);
  if (!architectId) {
    ui.error = 'engineer not found in scope';
    _agentPanelRefreshAfterSpecializationEdit();
    return;
  }
  var catalog = _agentPanelSpecializationCatalog(agent.group || '');
  var specializations = _agentPanelNormalizeSpecializations(
    ui.draft,
    catalog.names,
    catalog.matchesGroup
  );
  ui.draft = specializations.slice();
  ui.saving = true;
  ui.error = '';
  if (typeof send === 'function') {
    send({
      cmd: 'architect_engineer_set_specializations',
      architect_id: architectId,
      engineer_id: engineerId,
      specializations: specializations,
    });
  }
  _agentPanelRefreshAfterSpecializationEdit();
}

function _agentPanelSpecializationsVisibleForFocused(agent) {
  var focused = agent || (typeof _resolveFocusedAgent === 'function' ? _resolveFocusedAgent() : null);
  if (!focused) return false;
  if (_agentPanelKind(focused) === 'engineer') {
    return !!_agentPanelEngineerSpecializationsArchitectId(focused);
  }
  return false;
}

function agentPanelRenderEngineerSpecializationsEditor() {
  if (!_agentPanelSpecializationsVisibleForFocused()) return;
  _agentPanelRefreshAfterSpecializationEdit();
}

function agentPanelReceiveEngineerSpecializations(msg) {
  msg = msg || {};
  var engineerId = String(msg.engineer_id || '').trim();
  if (!engineerId) return;
  var specializations = Array.isArray(msg.specializations)
    ? msg.specializations.slice()
    : [];
  var cell = state && state.agents ? state.agents[engineerId] : null;
  if (cell) cell.engineer_specializations = specializations;
  var ui = _agentPanelEngineerSpecializationEditors[engineerId];
  if (ui) {
    var wasSaving = !!ui.saving;
    ui.saving = false;
    if (ui.editing && !wasSaving) {
      // Preserve an unsaved local draft across unrelated response/delta
      // traffic; only a save response should collapse the editor.
    } else {
      ui.error = '';
      ui.draft = specializations.slice();
      ui.editing = false;
    }
  }
  var focused = typeof _resolveFocusedAgent === 'function'
    ? _resolveFocusedAgent()
    : null;
  if (focused) {
    var focusedId = String(focused.id || '');
    var focusedKind = _agentPanelKind(focused);
    if (focusedId === engineerId
        || (focusedKind === 'architect'
          && cell
          && String(cell.hired_by_architect_id || '') === focusedId)) {
      _agentPanelRefreshAfterSpecializationEdit();
    }
  }
}

function agentPanelHandleEngineerSpecializationsError(msg) {
  var message = String((msg && (msg.message || msg.error)) || '').trim();
  if (!message) return false;
  var handled = false;
  for (var engineerId in _agentPanelEngineerSpecializationEditors) {
    var ui = _agentPanelEngineerSpecializationEditors[engineerId];
    if (!ui || !ui.saving) continue;
    ui.saving = false;
    ui.error = message;
    handled = true;
  }
  if (!handled) return false;
  if (typeof _showToast === 'function') _showToast(message, 'error');
  _agentPanelRefreshAfterSpecializationEdit();
  return true;
}

function _agentPanelTabRenderParts(agent, kind, activeTab) {
  var parts = { bodyHtml: '', headerRightHtml: '' };
  if (kind === 'engineer') {
    if (activeTab === 'behavior') {
      parts.bodyHtml = typeof renderBehaviorOverlayTab === 'function'
        ? renderBehaviorOverlayTab(agent)
        : '<div class="agent-panel-empty">Behavior overlay UI is unavailable.</div>';
    } else if (activeTab === 'events') {
      parts.headerRightHtml = _agentPanelDigestHeaderRight(agent);
      parts.bodyHtml += _renderEngineerEvents(agent);
    } else if (activeTab === 'queued') {
      parts.bodyHtml += _renderEngineerQueuedTasks(agent);
    } else if (activeTab === 'worklog') {
      parts.bodyHtml += _renderEngineerWorklog(agent);
    } else {
      parts.bodyHtml += _renderEngineerJournal(agent);
    }
    return parts;
  }
  if (kind === 'worker') {
    parts.bodyHtml = (activeTab === 'worklog')
      ? _agentPanelWorkerWorklog(agent)
      : (activeTab === 'messages')
      ? _agentPanelWorkerMessages(agent)
      : _renderAgentEventsWithInnerTabs(agent);
    return parts;
  }
  if (kind === 'architect') {
    if (activeTab === 'behavior') {
      parts.bodyHtml = typeof renderBehaviorOverlayTab === 'function'
        ? renderBehaviorOverlayTab(agent)
        : '<div class="agent-panel-empty">Behavior overlay UI is unavailable.</div>';
    } else if (activeTab === 'messages') {
      parts.bodyHtml = _agentPanelArchitectMessages(agent);
    } else if (activeTab === 'events') {
      parts.headerRightHtml = _agentPanelDigestHeaderRight(agent);
      parts.bodyHtml = _renderArchitectEvents(agent);
    } else if (activeTab === 'journal') {
      parts.bodyHtml = _agentPanelArchitectJournalHtml(agent);
    } else {
      parts.bodyHtml = _agentPanelArchitectDecisionsHtml(agent);
    }
    return parts;
  }
  return parts;
}

function _renderEngineerPanel(agent) {
  var activeTab = _agentPanelActiveTab('engineer');
  var parts = _agentPanelTabRenderParts(agent, 'engineer', activeTab);
  var bodyHtml = _agentPanelBodyWithClassManager(
    agent,
    _agentPanelEngineerSpecializationsEditorHtml(agent) + (parts.bodyHtml || ''),
    activeTab === 'behavior'
  );
  return _agentPanelShell(
    _agentPanelRoleTitle(agent, 'Engineer'),
    'Journal, digest queue, assigned tasks, and completed work for this engineer\'s group.',
    'engineer',
    activeTab,
    bodyHtml,
    (parts.headerRightHtml || ''),
    (agent && agent.id) || '',
    _agentPanelUpwardBreadcrumbHtml(agent)
  );
}

function _agentPanelWorkerEvents(agent) {
  var events = _agentPanelSortedWorkerEvents(agent);
  var page = _agentPanelEventPage(agent, events);

  var html = '<div class="agent-panel-event-section">';
  html += '<div class="agent-panel-event-section-header">';
  html += '<span class="agent-panel-event-section-title">Worker events</span>';
  html += '<span class="agent-panel-event-section-count">' + _agentPanelEventSectionCount(page) + '</span>';
  html += '</div>';
  if (!events.length) {
    html += '<div class="agent-panel-event-empty">No worker events yet.</div>';
    html += '</div>';
    return html;
  }
  html += '<div class="agent-panel-event-list">';
  for (var i = 0; i < page.events.length; i++) {
    var evt = page.events[i] || {};
    var anchorKey = 'worker-event-' + String(evt.id || i);
    var kind = typeof _engineerEventKindLabel === 'function'
      ? _engineerEventKindLabel(evt.kind)
      : String(evt.kind || 'event').replace(/_/g, ' ');
    var summary = evt.message || kind;
    html += '<div class="agent-panel-event-item agent-panel-event-item-sent" data-agent-panel-anchor="'
      + _agentPanelEsc(anchorKey) + '">';
    html += '<div class="agent-panel-event-item-header">';
    html += '<span class="agent-panel-event-kind">' + _agentPanelEsc(kind) + '</span>';
    html += '<span class="agent-panel-event-meta">' + _agentPanelEsc(_agentPanelTimeAgo(evt.timestamp)) + '</span>';
    html += '</div>';
    html += '<div class="agent-panel-event-message">' + _agentPanelEsc(summary) + '</div>';
    if (evt.task_id) {
      html += '<div class="agent-panel-event-task">' + _agentPanelEsc(evt.task_id) + '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  html += _agentPanelRenderEventLoadMore(page);
  html += '</div>';
  return html;
}

function _agentPanelWorkerTaskEntries(agent) {
  var agentId = String((agent && agent.id) || '');
  var tasks = [];
  if (!agentId || !state || !state.board_tasks) return tasks;
  var boardTaskCount = 0;
  for (var countTaskId in state.board_tasks) {
    if (Object.prototype.hasOwnProperty.call(state.board_tasks, countTaskId)) {
      boardTaskCount++;
    }
  }
  var cachedIds = _agentPanelWorkerTaskIdCacheByAgent[agentId];
  if (cachedIds && cachedIds._boardTaskCount !== boardTaskCount) {
    cachedIds = null;
  }
  if (!cachedIds) {
    cachedIds = [];
    for (var taskId in state.board_tasks) {
      var candidate = state.board_tasks[taskId];
      if (!candidate) continue;
      if (String(candidate.agent_id || '') !== agentId) continue;
      cachedIds.push(taskId);
    }
    cachedIds.sort(function(aId, bId) {
      var a = state.board_tasks[aId];
      var b = state.board_tasks[bId];
      var aTs = Number((a && (a.started_at || a.created_at || a.updated_at)) || 0);
      var bTs = Number((b && (b.started_at || b.created_at || b.updated_at)) || 0);
      if (aTs !== bTs) return bTs - aTs;
      return String((b && b.id) || bId || '').localeCompare(String((a && a.id) || aId || ''));
    });
    cachedIds._boardTaskCount = boardTaskCount;
    _agentPanelWorkerTaskIdCacheByAgent[agentId] = cachedIds;
  }
  for (var i = 0; i < cachedIds.length; i++) {
    var task = state.board_tasks[cachedIds[i]];
    if (task && String(task.agent_id || '') === agentId) tasks.push(task);
  }
  return tasks;
}

function _agentPanelInvalidateWorkerTaskCacheForTask(previous, next) {
  var ids = {};
  if (previous && previous.agent_id) ids[String(previous.agent_id)] = true;
  if (next && next.agent_id) ids[String(next.agent_id)] = true;
  for (var agentId in ids) {
    delete _agentPanelWorkerTaskIdCacheByAgent[agentId];
  }
}

function _agentPanelInvalidateWorkerTaskCacheForDeltas(changes) {
  if (!Array.isArray(changes)) return;
  for (var i = 0; i < changes.length; i++) {
    var change = changes[i] || {};
    _agentPanelInvalidateWorkerTaskCacheForTask(change.previous, change.next);
  }
}

function _agentPanelRenderWorkerWorklogItem(agent, task) {
  var taskId = (task && task.id) || '';
  var title = (task && (task.task || task.title)) || taskId || 'Task';
  var lane = (task && task.lane) || 'Not on board';
  var status = task ? String(task.status || '').trim() : '';
  var startedAt = Number((task && (task.started_at || task.created_at || task.updated_at)) || 0);
  var agentName = (agent && (agent.name || agent.slug || agent.id)) || 'Worker';
  var meta = startedAt ? ('started ' + _agentPanelTimeAgo(startedAt)) : 'recent task';
  var anchorKey = 'worker-task-' + String(taskId || title);

  var html = '<div class="agent-panel-worklog-item" data-agent-panel-anchor="' + _agentPanelEsc(anchorKey) + '">';
  html += '<div class="agent-panel-worklog-item-header">';
  html += '<div class="agent-panel-worklog-task">';
  html += '<div class="agent-panel-worklog-task-title">' + _agentPanelEsc(title) + '</div>';
  if (taskId) {
    html += '<div class="agent-panel-worklog-task-id">' + _agentPanelEsc(taskId) + '</div>';
  }
  html += '</div>';
  html += '<div class="agent-panel-worklog-lane">' + _agentPanelEsc(lane) + '</div>';
  html += '</div>';
  html += '<div class="agent-panel-worklog-meta-row">';
  html += '<span class="agent-panel-worklog-agent">' + _agentPanelEsc(agentName) + '</span>';
  html += '<span class="agent-panel-worklog-meta">' + _agentPanelEsc(meta) + '</span>';
  html += '</div>';
  if (status) {
    html += '<div class="agent-panel-worklog-status">' + _agentPanelEsc(status) + '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelWorkerWorklog(agent) {
  var tasks = _agentPanelWorkerTaskEntries(agent);
  var html = '<div class="agent-panel-worklog-tab">';
  html += '<div class="agent-panel-worklog-header">';
  html += '<span class="agent-panel-worklog-title">Task history</span>';
  html += '<span class="agent-panel-worklog-count">' + tasks.length + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-worklog-note">Tasks assigned to this worker.</div>';
  if (!tasks.length) {
    html += '<div class="agent-panel-event-empty">No tasks yet.</div>';
    html += '</div>';
    return html;
  }
  html += _agentPanelRenderVirtualList({
    key: _agentPanelFocusedSurfaceKey(agent, 'worklog', 'worker-tasks'),
    total: tasks.length,
    rowHeight: _AGENT_PANEL_WORKLOG_ROW_HEIGHT,
    listClass: 'agent-panel-worklog-list',
    scrollSelector: '.agent-panel-content',
    renderItem: function(index) {
      return _agentPanelRenderWorkerWorklogItem(agent, tasks[index]);
    },
  });
  html += '</div>';
  return html;
}

function _renderWorkerPanel(agent) {
  var activeTab = _agentPanelActiveTab('worker');
  var parts = _agentPanelTabRenderParts(agent, 'worker', activeTab);
  return _agentPanelShell(
    _agentPanelRoleTitle(agent, 'Worker'),
    'Per-worker event stream and task history.',
    'worker',
    activeTab,
    _agentPanelBodyWithClassManager(agent, parts.bodyHtml, activeTab === 'behavior'),
    (parts.headerRightHtml || ''),
    (agent && agent.id) || '',
    _agentPanelUpwardBreadcrumbHtml(agent)
  );
}

function _agentPanelArchitectDecisionRows(decisions) {
  decisions = Array.isArray(decisions) ? decisions : [];
  if (typeof _engineerDecisionGroups === 'function'
      && typeof _ENGINEER_DECISION_STATUSES !== 'undefined') {
    var grouped = _engineerDecisionGroups(decisions);
    var groupedRows = [];
    for (var statusIndex = 0; statusIndex < _ENGINEER_DECISION_STATUSES.length; statusIndex++) {
      var statusName = _ENGINEER_DECISION_STATUSES[statusIndex];
      var rows = grouped[statusName] || [];
      for (var rowIndex = 0; rowIndex < rows.length; rowIndex++) {
        groupedRows.push({
          status: statusName,
          decision: rows[rowIndex],
        });
      }
    }
    return groupedRows;
  }
  return decisions.map(function(decision) {
    return {
      status: String((decision && decision.status) || 'proposed'),
      decision: decision,
    };
  });
}

function _agentPanelArchitectDecisionItemHtml(agent, rows, index) {
  var row = rows[index] || {};
  var statusName = row.status || 'proposed';
  var previous = rows[index - 1] || {};
  var showStatus = index === 0 || previous.status !== statusName;
  var html = '';
  if (showStatus) {
    html += '<div class="architect-decision-group-title">'
      + _agentPanelEsc(statusName) + '</div>';
  }
  if (typeof _agentPanelLegacyRenderDecisionRow === 'function'
      && typeof _ENGINEER_DECISION_STATUSES !== 'undefined') {
    html += _agentPanelLegacyRenderDecisionRow(agent.id, row.decision);
    return html;
  }
  var decision = row.decision || {};
  var archived = _agentPanelDecisionIsArchived(decision);
  html += '<div class="detail-section-card architect-decision-card'
    + (archived ? ' architect-decision-card-archived' : '')
    + '" data-agent-panel-anchor="decision-'
    + _agentPanelEsc(decision.id || index) + '">';
  html += '<div class="detail-section-card-head">';
  html += '<span class="detail-section-primary">' + _agentPanelEsc(decision.title || 'Decision') + '</span>';
  html += '<span class="detail-task-status">' + _agentPanelEsc(decision.status || 'proposed') + '</span>';
  if (archived) html += '<span class="architect-decision-archive-badge">Archived</span>';
  html += '</div>';
  if (decision.rationale) {
    html += '<div class="detail-section-card-body">' + _agentPanelEsc(decision.rationale) + '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelArchitectDecisionsHtml(agent) {
  var architectId = String((agent && agent.id) || '').trim();
  if (typeof lazyLoadDecisions === 'function') {
    lazyLoadDecisions({ include_archived: true });
  }
  var counts = _agentPanelArchitectDecisionCounts(architectId);
  var showArchived = _agentPanelShowArchivedDecisions(architectId);
  var decisions = _agentPanelArchitectDecisionList(architectId, {
    include_archived: showArchived,
  });
  var html = '<div class="agent-panel-worklog-tab">';
  html += '<div class="agent-panel-worklog-header agent-panel-decisions-header">';
  html += '<div class="agent-panel-decisions-heading">';
  html += '<span class="agent-panel-worklog-title">Decisions</span>';
  html += '<span class="agent-panel-worklog-count agent-panel-decisions-count">'
    + counts.active + ' active &middot; ' + counts.archived + ' archived</span>';
  html += '</div>';
  html += '<button type="button" class="agent-panel-decisions-archive-toggle" aria-pressed="'
    + (showArchived ? 'true' : 'false') + '" onclick="'
    + _agentPanelEventAttr('agentPanelToggleArchivedDecisions(event,' + JSON.stringify(architectId) + ')')
    + '">' + (showArchived ? 'Hide archived' : 'Show archived') + '</button>';
  html += '</div>';
  if (!decisions.length) {
    var emptyText = counts.archived > 0 && !showArchived
      ? ('No active decisions. ' + counts.archived + ' archived hidden.')
      : 'No decisions yet.';
    html += '<div class="agent-panel-event-empty">' + _agentPanelEsc(emptyText) + '</div>';
    html += '</div>';
    return html;
  }

  var rows = _agentPanelArchitectDecisionRowsForAgent(architectId, {
    include_archived: showArchived,
  });
  html += _agentPanelRenderVirtualList({
    key: _agentPanelFocusedSurfaceKey(agent, 'decisions', 'decisions'),
    total: rows.length,
    rowHeight: _AGENT_PANEL_DECISION_ROW_HEIGHT,
    listClass: 'architect-decision-group',
    scrollSelector: '.agent-panel-content',
    renderItem: function(index) {
      return _agentPanelArchitectDecisionItemHtml(agent, rows, index);
    },
  });
  html += '</div>';
  return html;
}

function _agentPanelMessagesHtml(agent, messages, note, options) {
  messages = Array.isArray(messages) ? messages : [];
  options = options || {};
  var html = '<div class="agent-panel-messages-tab">';
  html += '<div class="agent-panel-message-header">';
  html += '<div class="agent-panel-message-heading">';
  html += '<span class="agent-panel-message-title">Messages</span>';
  html += '<span class="agent-panel-message-count">' + messages.length + '</span>';
  html += '</div>';
  if (note) {
    html += '<div class="agent-panel-message-note">' + _agentPanelEsc(note) + '</div>';
  }
  html += '</div>';
  if (options.composeHtml) html += options.composeHtml;
  if (!messages.length) {
    html += '<div class="agent-panel-event-empty">No messages yet.</div>';
    html += '</div>';
    return html;
  }
  html += _agentPanelRenderVirtualList({
    key: _agentPanelFocusedSurfaceKey(agent, 'messages', 'messages'),
    total: messages.length,
    rowHeight: _AGENT_PANEL_MESSAGE_ROW_HEIGHT,
    listClass: 'agent-panel-message-list',
    scrollSelector: '.agent-panel-message-list',
    renderItem: function(index) {
      var message = messages[index] || {};
      return _agentPanelMessageCardHtml(agent, message, index);
    },
  });
  html += '</div>';
  return html;
}

function _agentPanelArchitectMessages(agent) {
  return _agentPanelMessagesHtml(
    agent,
    _agentPanelArchitectMessageList(agent),
    ''
  );
}

function _agentPanelWorkerMessages(agent) {
  return _agentPanelMessagesHtml(
    agent,
    _agentPanelInlineThreadMessageList(agent),
    'Inline Engineer messages stored on this worker’s current tasks.'
  );
}

function _agentPanelArchitectJournalEntryHtml(entry, index) {
  entry = entry || {};
  var anchorKey = 'architect-journal-' + String(entry.id || index);
  var entryType = String(entry.type || 'observation');
  var typeClass = 'agent-panel-badge-' + entryType.replace(/[^a-z0-9_-]/gi, '').toLowerCase();
  var canExpand = _agentPanelJournalTextExpandable(entry.entry);
  var html = '<div class="agent-panel-entry'
    + (canExpand ? ' agent-panel-journal-collapsible' : '')
    + '" data-agent-panel-anchor="'
    + _agentPanelEsc(anchorKey) + '">';
  html += '<div class="agent-panel-entry-header">';
  html += '<span class="agent-panel-badge ' + _agentPanelEsc(typeClass) + '">'
    + _agentPanelEsc(entryType) + '</span>';
  html += '<span class="agent-panel-entry-time">'
    + _agentPanelEsc(_agentPanelTimestamp(entry.timestamp)) + '</span>';
  html += '</div>';
  html += _agentPanelJournalBodyHtml(entry.entry, 'agent-panel-entry-text');
  html += '</div>';
  return html;
}

function _agentPanelJournalTextExpandable(text) {
  return String(text || '').split(/\r\n|\r|\n/).length > 5;
}

function _agentPanelJournalBodyHtml(text, className) {
  var expandable = _agentPanelJournalTextExpandable(text);
  var html = '<div class="' + _agentPanelEsc(className || '')
    + (expandable ? ' agent-panel-journal-clipped' : '') + '">'
    + _agentPanelEsc(text || '') + '</div>';
  if (expandable) {
    html += '<button type="button" class="agent-panel-journal-toggle"'
      + ' aria-expanded="false" onclick="agentPanelToggleJournalEntry(event,this)">'
      + 'Show more</button>';
  }
  return html;
}

function agentPanelToggleJournalEntry(event, button) {
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  if (!button) return false;
  var card = typeof button.closest === 'function'
    ? button.closest('.agent-panel-journal-collapsible')
    : null;
  if (!card || !card.classList) return false;
  var expanded = !card.classList.contains('agent-panel-journal-expanded');
  card.classList.toggle('agent-panel-journal-expanded', expanded);
  button.textContent = expanded ? 'Show less' : 'Show more';
  if (typeof button.setAttribute === 'function') {
    button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  }
  return false;
}

function _agentPanelArchitectJournalHtml(agent) {
  _agentPanelRequestArchitectJournal(agent);
  var architectId = String((agent && agent.id) || '');
  var entries = _agentPanelArchitectJournalEntries(agent);
  var visibleLimit = _agentPanelArchitectJournalVisibleLimit(architectId);
  var visibleEntries = entries.slice(0, Math.min(entries.length, visibleLimit));
  var decisionCount = _agentPanelArchitectDecisionList(architectId).length;
  var loading = !!_agentPanelArchitectJournalLoadingById[architectId];
  var latestCheckpoint = _agentPanelArchitectJournalLatestCheckpoint(entries);
  var countText = String(visibleEntries.length);
  if (visibleEntries.length < entries.length) {
    countText += ' / ' + entries.length;
  }

  var html = '<div class="agent-panel-worklog-tab">';
  html += '<div class="agent-panel-worklog-header">';
  html += '<span class="agent-panel-worklog-title">Journal</span>';
  html += '<span class="agent-panel-worklog-count" data-agent-panel-journal-count>'
    + _agentPanelEsc(countText) + '</span>';
  html += '<span class="agent-panel-worklog-note"> · '
    + _agentPanelEsc(decisionCount + ' decision' + (decisionCount === 1 ? '' : 's'))
    + '</span>';
  html += '</div>';

  if (latestCheckpoint) {
    var checkpointCanExpand = _agentPanelJournalTextExpandable(latestCheckpoint.entry);
    html += '<div class="detail-section-card agent-panel-checkpoint-card'
      + (checkpointCanExpand ? ' agent-panel-journal-collapsible' : '')
      + '" '
      + 'data-agent-panel-anchor="architect-journal-checkpoint">';
    html += '<div class="detail-section-card-head">';
    html += '<span class="detail-section-primary">Current architect state</span>';
    html += '<span class="detail-task-status">'
      + _agentPanelEsc(_agentPanelTimestamp(latestCheckpoint.timestamp))
      + '</span>';
    html += '</div>';
    html += _agentPanelJournalBodyHtml(latestCheckpoint.entry, 'detail-section-card-body');
    html += '</div>';
  }

  if (loading && !entries.length) {
    html += '<div class="agent-panel-empty">Loading architect journal…</div>';
    html += '</div>';
    return html;
  }
  if (!entries.length) {
    html += '<div class="agent-panel-empty">No journal entries yet.</div>';
    html += '</div>';
    return html;
  }

  html += '<div class="agent-panel-journal">';
  for (var i = 0; i < visibleEntries.length; i++) {
    html += _agentPanelArchitectJournalEntryHtml(visibleEntries[i], i);
  }
  html += '</div>';
  if (loading && visibleEntries.length) {
    html += '<div class="agent-panel-worklog-note">Loading older journal entries…</div>';
  }
  if (_agentPanelArchitectJournalCanLoadOlder(architectId, entries, visibleLimit)) {
    html += '<button type="button" class="agent-panel-event-load-more"'
      + ' onclick="agentPanelLoadOlderArchitectJournal(event, \''
      + _agentPanelEsc(architectId) + '\')">'
      + _agentPanelEsc(_agentPanelArchitectJournalLoadMoreLabel(entries, visibleLimit))
      + '</button>';
  }
  html += '</div>';
  return html;
}

function _renderArchitectPanel(agent) {
  var activeTab = _agentPanelActiveTab('architect');
  var parts = _agentPanelTabRenderParts(agent, 'architect', activeTab);
  return _agentPanelShell(
    _agentPanelRoleTitle(agent, 'Architect'),
    'Journal, decisions, hired engineers, architect messages, and digest queue.',
    'architect',
    activeTab,
    _agentPanelBodyWithClassManager(agent, parts.bodyHtml, activeTab === 'behavior'),
    (parts.headerRightHtml || ''),
    (agent && agent.id) || '',
    _agentPanelUpwardBreadcrumbHtml(agent)
  );
}

function _renderUserPanel(agent) {
  var group = String((agent && agent.group) || '');
  var body = _agentPanelLegacyRenderEngineerRoster(group);
  return _agentPanelShell(
    'User' + (group ? ' · Group: ' + group : ''),
    'User-owned engineers and workers.',
    'user',
    '',
    body,
    '',
    (agent && agent.id) || 'user',
    _agentPanelUpwardBreadcrumbHtml(agent)
  );
}

function _agentPanelTerminalValue(value, fallback) {
  var text = String(value || '').trim();
  return text || String(fallback || '—');
}

function _renderTerminalPanel(agent) {
  var branch = String((agent && (agent.worktree_branch || agent.current_branch)) || '').replace(/^torque\//, '');
  var processInfo = (typeof _terminalStatusLabel === 'function')
    ? _terminalStatusLabel(agent)
    : ((agent && (agent.current_process || agent.activity_detail || agent.activity || agent.status)) || 'idle');
  var displayPath = (typeof _terminalDisplayPath === 'function')
    ? _terminalDisplayPath(agent)
    : ((agent && (agent.current_path || agent.directory)) || '');
  var body = '<div class="detail-section-card">';
  body += '<div class="detail-row"><span class="detail-label">Agent</span><span class="detail-val">'
    + _agentPanelEsc(_agentPanelTerminalValue(agent && (agent.name || agent.id), 'Terminal')) + '</span></div>';
  body += '<div class="detail-row"><span class="detail-label">Branch</span><span class="detail-val detail-branch">\u2387 '
    + _agentPanelEsc(_agentPanelTerminalValue(branch)) + '</span></div>';
  body += '<div class="detail-row"><span class="detail-label">Process</span><span class="detail-val">'
    + _agentPanelEsc(_agentPanelTerminalValue(processInfo, 'idle')) + '</span></div>';
  body += '<div class="detail-row"><span class="detail-label">Path</span><span class="detail-val" title="'
    + _agentPanelEsc(displayPath || '') + '">' + _agentPanelEsc(_agentPanelTerminalValue(displayPath)) + '</span></div>';
  body += '<div class="agent-panel-worklog-note">Open the terminal drawer to interact with this session.</div>';
  body += '</div>';
  return _agentPanelShell(
    'Terminal: ' + ((agent && (agent.name || agent.id)) || 'Terminal'),
    'Terminal session status.',
    'terminal',
    '',
    body,
    '',
    (agent && agent.id) || ''
  );
}

function _agentPanelBuildPanelStateOptions(agent, activeTab, virtualMetas) {
  virtualMetas = virtualMetas || [];
  var scrollSelectors = ['.agent-panel-content', '.agent-panel-message-list'];
  if (activeTab === 'events' && _agentPanelEventsInnerTab(agent) === 'mcp') {
    scrollSelectors.push('.agent-panel-mcp-list');
  }
  var panelStateOptions = {
    scrollSelectors: scrollSelectors,
    capture: function(snapshot, root) {
      if (!snapshot || !root || typeof root.querySelector !== 'function') return;
      if (agent && _agentPanelActiveTab(_agentPanelKind(agent)) === 'events') {
        snapshot.agentPanelEventsAtLiveTail = _agentPanelEventsAtLiveTail(
          _agentPanelScrollContainer(root)
        );
      }
      _agentPanelCaptureVirtualScrolls(root, virtualMetas);
      snapshot.anchor = _agentPanelCaptureScrollAnchor(
        _agentPanelScrollContainer(root)
      );
    },
    restore: function(root, snapshot) {
      if (!root || !snapshot || typeof root.querySelector !== 'function') return;
      if (snapshot.agentPanelEventsAtLiveTail) {
        var liveTailContainer = _agentPanelScrollContainer(root);
        if (liveTailContainer && typeof liveTailContainer.scrollTop === 'number') {
          liveTailContainer.scrollTop = 0;
        }
        return;
      }
      _agentPanelRestoreScrollAnchor(
        _agentPanelScrollContainer(root),
        snapshot.anchor
      );
    },
  };
  if (typeof _captureMainFocusKey === 'function') {
    panelStateOptions.captureFocusKey = _captureMainFocusKey;
  }
  return panelStateOptions;
}

function _agentPanelShellMatches(shell, agent, kind) {
  if (!shell || !agent) return false;
  var expectedAgentId = String((agent && agent.id) || '');
  var expectedKind = String(kind || '');
  var shellAgentId = '';
  var shellKind = '';
  if (shell.dataset) {
    shellAgentId = String(shell.dataset.agentPanelAgentId || '');
    shellKind = String(shell.dataset.agentPanelKind || '');
  }
  if (!shellAgentId && typeof shell.getAttribute === 'function') {
    shellAgentId = String(shell.getAttribute('data-agent-panel-agent-id') || '');
  }
  if (!shellKind && typeof shell.getAttribute === 'function') {
    shellKind = String(shell.getAttribute('data-agent-panel-kind') || '');
  }
  return shellAgentId === expectedAgentId && shellKind === expectedKind;
}

function _agentPanelSetShellTab(shell, activeTab) {
  if (!shell) return;
  if (typeof shell.setAttribute === 'function') {
    shell.setAttribute('data-agent-panel-tab', activeTab || '');
  } else if (shell.dataset) {
    shell.dataset.agentPanelTab = String(activeTab || '');
  }
}

function _agentPanelSetActiveTabChrome(root, activeTab) {
  if (!root || typeof root.querySelectorAll !== 'function') return;
  var buttons = root.querySelectorAll('.agent-panel-tab') || [];
  for (var i = 0; i < buttons.length; i++) {
    var btn = buttons[i];
    var tabKey = '';
    if (btn.dataset) tabKey = String(btn.dataset.agentPanelTabKey || '');
    if (!tabKey && typeof btn.getAttribute === 'function') {
      tabKey = String(btn.getAttribute('data-agent-panel-tab-key') || '');
    }
    if (!tabKey && btn.id) tabKey = String(btn.id).replace(/^agent-panel-tab-/, '');
    if (!btn.classList) continue;
    if (tabKey === activeTab) btn.classList.add('active');
    else btn.classList.remove('active');
  }
}

function _agentPanelHeaderRight(root) {
  if (!root || typeof root.querySelector !== 'function') return null;
  return root.querySelector('[data-agent-panel-header-right]')
    || root.querySelector('.agent-panel-header-right');
}

function _agentPanelRenderFocusedTabInPlace(agent, kind, previousTab, activeTab) {
  // TORQUE:236 v8/v11 instrumentation: enable with `window.__torqueDebugRender = true;`
  // v11 adds caller stack so the user can identify which path is still
  // firing in-place refreshes after v10's render-path skip.
  if (typeof window !== 'undefined' && window.__torqueDebugRender) {
    try {
      var stk = (new Error()).stack || '';
      console.log('[torque render] inPlace @' + Date.now()
        + ' kind=' + kind + ' tab=' + activeTab
        + ' agent=' + ((agent && agent.id) || '')
        + '\n' + stk.split('\n').slice(2, 8).join('\n'));
    } catch (_e) {}
  }
  var el = document.getElementById('panel-agent');
  if (!el || !agent || kind === 'terminal') return false;
  if (typeof _engineerStopEventsCountdownTimer === 'function') {
    _engineerStopEventsCountdownTimer();
  }
  var shell = (typeof el.querySelector === 'function')
    ? el.querySelector('.agent-panel-panel')
    : null;
  var content = (typeof el.querySelector === 'function')
    ? el.querySelector('.agent-panel-content')
    : null;
  var headerRight = _agentPanelHeaderRight(el);
  if (!shell || !content || !headerRight || !_agentPanelShellMatches(shell, agent, kind)) {
    return false;
  }
  var _torqueRenderStart = (typeof performance !== 'undefined' && performance && typeof performance.now === 'function')
    ? performance.now()
    : (Date.now ? Date.now() : 0);

  _agentPanelEventsEnsurePager(agent);
  var switchingTabs = previousTab !== activeTab;
  var previousMetas = _agentPanelVirtualMetasForSurface(agent, previousTab);
  var nextMetas = _agentPanelVirtualMetasForSurface(agent, activeTab);
  var panelStateOptions = _agentPanelBuildPanelStateOptions(agent, activeTab, nextMetas);
  var panelState = null;
  if (!switchingTabs && typeof _captureSurfaceState === 'function') {
    panelState = _captureSurfaceState(el, panelStateOptions);
  } else {
    _agentPanelCaptureVirtualScrolls(el, previousMetas);
  }
  _agentPanelEventsPreRenderAtLiveTail = !!(
    panelState && panelState.agentPanelEventsAtLiveTail
  );

  _agentPanelRenderedVirtualMetas = [];
  var parts = _agentPanelTabRenderParts(agent, kind, activeTab);
  if (kind === 'engineer') {
    parts.bodyHtml = _agentPanelEngineerSpecializationsEditorHtml(agent)
      + (parts.bodyHtml || '');
  }
  parts.bodyHtml = _agentPanelBodyWithClassManager(agent, parts.bodyHtml || '', activeTab === 'behavior');
  _agentPanelSetShellTab(shell, activeTab);
  _agentPanelSetActiveTabChrome(el, activeTab);
  // TORQUE:264 follow-up: byte-equality memoize the innerHTML clobber. Under
  // multi-agent firehose this function fires dozens of times/sec; when the
  // rendered html is identical to the last paint the assignment still
  // destroys + recreates every child node, killing :hover state on tooltip
  // pseudo-elements and resetting textarea caret. Same `_torqueLastHtml`
  // pattern as `dom.topbar` / `dom.tabs` from `06611b8`.
  var newHeaderHtml = (parts.headerRightHtml || '');
  var newBodyHtml = parts.bodyHtml || '';
  var headerChanged = headerRight._torqueLastHtml !== newHeaderHtml;
  var bodyChanged = content._torqueLastHtml !== newBodyHtml;
  if (headerChanged) {
    headerRight.innerHTML = newHeaderHtml;
    headerRight._torqueLastHtml = newHeaderHtml;
  }
  if (bodyChanged) {
    content.innerHTML = newBodyHtml;
    content._torqueLastHtml = newBodyHtml;
  }
  // Invalidate the root `el._torqueLastHtml` cache when this surgical path
  // mutates a child. Otherwise a later `renderAgentPanel()` whose computed
  // html happens to byte-equal the cache (e.g. matches the pre-mutation
  // full render) skips its `el.innerHTML = html` write and leaves the
  // surgical-overwritten children in the DOM — stale content visible to
  // the user. Reviewer reproduced via Node harness:
  //   1. full render writes htmlA, caches htmlA on el
  //   2. in-place refresh writes htmlB into a child
  //   3. state reverts; full render computes htmlA, gate skips, DOM stays
  //      at htmlB.
  if ((headerChanged || bodyChanged) && el._torqueLastHtml !== undefined) {
    el._torqueLastHtml = null;
  }

  if (!switchingTabs && typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(el, panelState, panelStateOptions);
  } else {
    _agentPanelRestoreVirtualScrolls(el, nextMetas);
  }
  _agentPanelDetachVirtualScrollsForRoot(el);
  _agentPanelDetachEventsScroll(el);
  _agentPanelAttachVirtualScrolls(el);
  _agentPanelAttachEventsScroll(el, agent);
  _agentPanelRenderClassModal();
  _agentPanelEventsPreRenderAtLiveTail = false;
  if (agent
      && (kind === 'engineer' || kind === 'architect')
      && typeof _engineerSyncEventsCountdown === 'function') {
    _engineerSyncEventsCountdown(el, agent.group || '', activeTab);
  }
  if (typeof healthRecordFrontendRender === 'function') {
    var _torqueRenderEnd = (typeof performance !== 'undefined' && performance && typeof performance.now === 'function')
      ? performance.now()
      : (Date.now ? Date.now() : _torqueRenderStart);
    healthRecordFrontendRender(Math.max(0, _torqueRenderEnd - _torqueRenderStart), 'agent-panel-in-place');
  }
  return true;
}

function _agentPanelRefreshCurrentTab() {
  var agent = _resolveFocusedAgent();
  if (!agent) return false;
  var kind = _agentPanelKind(agent);
  var activeTab = _agentPanelActiveTab(kind);
  return _agentPanelRenderFocusedTabInPlace(agent, kind, activeTab, activeTab);
}

function renderAgentPanel() {
  // TORQUE:236 v8 instrumentation: enable in browser devtools console
  // with `window.__torqueDebugRender = true;` to log every full panel
  // rebuild + the calling stack. Use to identify residual firehose
  // sources after the v4-v7 invalidation gates.
  if (typeof window !== 'undefined' && window.__torqueDebugRender) {
    try {
      var stk = (new Error()).stack || '';
      console.warn('[torque render] renderAgentPanel @' + Date.now()
        + ' caller:\n' + stk.split('\n').slice(2, 8).join('\n'));
    } catch (_e) {}
  }
  if (typeof _engineerStopEventsCountdownTimer === 'function') {
    _engineerStopEventsCountdownTimer();
  }
  var el = document.getElementById('panel-agent');
  if (!el) return;
  var _torqueRenderStart = (typeof performance !== 'undefined' && performance && typeof performance.now === 'function')
    ? performance.now()
    : (Date.now ? Date.now() : 0);
  var agent = _resolveFocusedAgent();
  if (agent && agent.group && typeof lazyLoadEngineerJournal === 'function') {
    lazyLoadEngineerJournal(agent.group);
  }
  _agentPanelEventsEnsurePager(agent);
  var agentKindForRender = agent ? _agentPanelKind(agent) : '';
  var activeTabForRender = agent ? _agentPanelActiveTab(agentKindForRender) : '';
  var virtualMetasForRender = _agentPanelVirtualMetasForSurface(agent, activeTabForRender);
  var panelStateOptions = _agentPanelBuildPanelStateOptions(
    agent,
    activeTabForRender,
    virtualMetasForRender
  );

  var panelState = typeof _captureSurfaceState === 'function'
    ? _captureSurfaceState(el, panelStateOptions)
    : null;
  _agentPanelEventsPreRenderAtLiveTail = !!(
    panelState && panelState.agentPanelEventsAtLiveTail
  );
  _agentPanelRenderedVirtualMetas = [];
  var html = '';

  if (!agent) {
    html = '<div class="agent-panel">'
      + '<div class="agent-panel-empty">Select an agent from the grid to see its context.</div>'
      + '</div>';
  } else {
    switch (_agentPanelKind(agent)) {
      case 'architect':
        html = _renderArchitectPanel(agent);
        break;
      case 'engineer':
        html = _renderEngineerPanel(agent);
        break;
      case 'user':
        html = _renderUserPanel(agent);
        break;
      case 'terminal':
        html = _renderTerminalPanel(agent);
        break;
      case 'worker':
      default:
        html = _renderWorkerPanel(agent);
        break;
    }
  }

  // TORQUE:264 follow-up: byte-equality memoize the full panel clobber. Same
  // pattern as the in-place tab refresh above.
  if (el._torqueLastHtml !== html) {
    el.innerHTML = html;
    el._torqueLastHtml = html;
  }
  if (typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(el, panelState, panelStateOptions);
  }
  _agentPanelDetachVirtualScrollsForRoot(el);
  _agentPanelDetachEventsScroll(el);
  _agentPanelAttachVirtualScrolls(el);
  _agentPanelAttachEventsScroll(el, agent);
  _agentPanelRenderClassModal();
  _agentPanelEventsPreRenderAtLiveTail = false;
  var agentKind = agent ? _agentPanelKind(agent) : '';
  if (agent
      && (agentKind === 'engineer' || agentKind === 'architect')
      && typeof _engineerSyncEventsCountdown === 'function') {
    _engineerSyncEventsCountdown(el, agent.group || '', _agentPanelActiveTab(agentKind));
  }
  if (typeof healthRecordFrontendRender === 'function') {
    var _torqueRenderEnd = (typeof performance !== 'undefined' && performance && typeof performance.now === 'function')
      ? performance.now()
      : (Date.now ? Date.now() : _torqueRenderStart);
    healthRecordFrontendRender(Math.max(0, _torqueRenderEnd - _torqueRenderStart), 'agent-panel-full');
  }
}


/* ---- Legacy engineer support moved into agent_panel.js (stage 5) ---- */
/* Agent panel — journal + events + worklog */

var _engineerReplyDraft = '';
var _engineerActiveTabByGroup = {};
var _engineerJournalSubviewByGroup = {};
var _engineerSessionMapMetaByGroup = {};
var _engineerHealthOrder = ['blocked', 'stale-in-progress', 'stalled', 'thrashing', 'idle-risk'];
var _engineerHealthLabels = {
  'blocked': 'Blocked',
  'stale-in-progress': 'Stale in progress',
  'stalled': 'Stalled',
  'thrashing': 'Thrashing',
  'idle-risk': 'Idle risk',
};
var _engineerVerificationLabels = {
  'pending': 'Verify pending',
  'attempted': 'Verify attempted',
  'passed': 'Verified',
  'failed': 'Verify failed',
};
var _engineerEventsCountdownTimer = 0;
var _engineerHealthSeverity = {
  'healthy': 0,
  'idle-risk': 1,
  'thrashing': 2,
  'stalled': 3,
  'stale-in-progress': 4,
  'blocked': 5,
};

function _engineerCreatedSortValue(cell) {
  if (!cell) return '';
  return String(cell.created_at || cell.updated_at || cell.id || '');
}

function _engineerSortByCreatedAt(a, b) {
  return _engineerCreatedSortValue(a).localeCompare(_engineerCreatedSortValue(b));
}

function _engineerSortArchitectsByCreatedAt(a, b) {
  return _engineerSortByCreatedAt(a, b);
}


var _engineerArchitectExpanded = {};
var _engineerArchitectDecisionUi = {};
var _ENGINEER_DECISION_STATUSES = ['proposed', 'accepted', 'revised', 'rejected'];

function _engineerDecisionFocusKey(decisionId, field) {
  return 'engineer-decision-' + String(field || '') + ':' + String(decisionId || '');
}

function _engineerGroupAgents(group) {
  var agents = [];
  if (!state || !state.agents) return agents;
  for (var agentId in state.agents) {
    var agent = state.agents[agentId];
    if (!agent || agent.cell_type !== 'agent') continue;
    if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(agent)) continue;
    if (group && agent.group !== group) continue;
    agents.push(agent);
  }
  agents.sort(_engineerSortByCreatedAt);
  return agents;
}

function _engineerArchitectAgents(group) {
  return _engineerGroupAgents(group).filter(function(agent) {
    return (agent.kind || '') === 'architect';
  }).sort(_engineerSortArchitectsByCreatedAt);
}

function _engineerEngineerAgents(group, architectId) {
  return _engineerGroupAgents(group).filter(function(agent) {
    if ((agent.kind || '') !== 'engineer') return false;
    var hiredByArchitectId = String(agent.hired_by_architect_id || '').trim();
    if (typeof architectId === 'undefined') return true;
    if (architectId === null) return !hiredByArchitectId;
    return hiredByArchitectId === String(architectId || '').trim();
  });
}

function _engineerWorkerOwnerId(agent) {
  return String(
    (agent && (agent.owner_engineer_id || agent.created_by_engineer_id)) || ''
  ).trim();
}

function _engineerWorkerAgents(group, engineerId) {
  return _engineerGroupAgents(group).filter(function(agent) {
    return (agent.kind || '') !== 'architect'
      && (agent.kind || '') !== 'engineer'
      && _engineerWorkerOwnerId(agent) === String(engineerId || '').trim();
  });
}

function _engineerAgentStatusLabel(agent) {
  if (agent && Number(agent.dismissed_at || 0) > 0) return 'dismissed';
  if (!agent || agent.status === 'stopped') return 'stopped';
  if (agent.activity || agent.activity_detail) return 'running';
  return 'idle';
}

function _agentPanelIsArchitectDismissed(architectId) {
  var key = String(architectId || '').trim();
  var architect = key && state && state.agents ? state.agents[key] : null;
  return !!(architect && Number(architect.dismissed_at || 0) > 0);
}

function _agentPanelDecisionArchitectId(decisionId) {
  var key = String(decisionId || '').trim();
  if (!key) return '';
  var stores = _agentPanelDecisionStores();
  for (var storeIndex = 0; storeIndex < stores.length; storeIndex++) {
    var store = stores[storeIndex] || {};
    var decision = Array.isArray(store)
      ? store.find(function(item) { return String((item && item.id) || '') === key; })
      : store[key];
    if (decision) return String(decision.architect_id || '').trim();
  }
  return '';
}

function _agentPanelBlockDismissedArchitectDecisionMutation(architectId) {
  if (!_agentPanelIsArchitectDismissed(architectId)) return false;
  if (typeof _showToast === 'function') {
    _showToast('Rehire architect to modify decisions', 'warning');
  }
  return true;
}

function _engineerEngineerStatusLabel(agent) {
  return _engineerAgentStatusLabel(agent);
}

function _engineerEngineerTransferCounts(engineerId) {
  var counts = { workers: 0, tasks: 0 };
  if (!engineerId) return counts;
  if (state && state.agents) {
    for (var agentId in state.agents) {
      var agent = state.agents[agentId];
      if (!agent || agent.id === engineerId || agent.cell_type !== 'agent') continue;
      if ((agent.owner_engineer_id || '') === engineerId
          || (agent.created_by_engineer_id || '') === engineerId) {
        counts.workers += 1;
      }
    }
  }
  if (state && state.board_tasks) {
    for (var taskId in state.board_tasks) {
      var task = state.board_tasks[taskId];
      if (task && (task.assigned_engineer_id || '') === engineerId) counts.tasks += 1;
    }
  }
  return counts;
}

function _engineerArchitectTransferCounts(architectId) {
  var counts = { engineers: 0, decisions: 0 };
  var architectKey = String(architectId || '').trim();
  if (!architectKey) return counts;
  counts.engineers = _engineerEngineerAgents(_agentPanelCurrentGroup(), architectKey).length;
  counts.decisions = _architectDecisionsForAgent(architectKey).length;
  return counts;
}

function _engineerArchitectExpandedState(architectId) {
  var key = String(architectId || '').trim();
  return !!_engineerArchitectExpanded[key];
}

function _engineerDecisionUiState(decisionId, decision) {
  var key = String(decisionId || '').trim();
  if (!_engineerArchitectDecisionUi[key]) {
    _engineerArchitectDecisionUi[key] = {
      expanded: false,
      editing: false,
      draft: { title: '', rationale: '', status: 'proposed' },
      link_task_id: '',
      link_engineer_id: '',
    };
  }
  var entry = _engineerArchitectDecisionUi[key];
  var current = decision
    || (state && state.decisions ? state.decisions[key] : null)
    || (state && state.architect_decisions ? state.architect_decisions[key] : null)
    || {};
  if (!entry.editing) {
    entry.draft.title = String(current.title || '');
    entry.draft.rationale = String(current.rationale || '');
    entry.draft.status = String(current.status || 'proposed');
  }
  return entry;
}

function _engineerMultilineHtml(text) {
  var formatter = (typeof formatCode === 'function') ? formatCode : _agentPanelEsc;
  return formatter(text || '').replace(/\n/g, '<br>');
}

function _engineerDecisionGroups(decisions) {
  var grouped = {};
  for (var i = 0; i < _ENGINEER_DECISION_STATUSES.length; i++) {
    grouped[_ENGINEER_DECISION_STATUSES[i]] = [];
  }
  for (var j = 0; j < decisions.length; j++) {
    var decision = decisions[j] || {};
    var status = String(decision.status || 'proposed');
    if (!grouped[status]) grouped[status] = [];
    grouped[status].push(decision);
  }
  Object.keys(grouped).forEach(function(status) {
    grouped[status].sort(_engineerDecisionRecencySort);
  });
  return grouped;
}

function getArchitectDecisionTaskOptions(architectId) {
  var architect = state && state.agents ? state.agents[String(architectId || '')] : null;
  var group = architect ? architect.group : '';
  var tasks = [];
  if (!state || !state.board_tasks) return tasks;
  for (var taskId in state.board_tasks) {
    var task = state.board_tasks[taskId];
    if (!task) continue;
    if (group && task.group !== group) continue;
    tasks.push({
      value: task.id,
      label: (task.task || task.id || '') + ' (' + (task.lane || 'task') + ')',
    });
  }
  tasks.sort(function(a, b) {
    return String(a.label || '').localeCompare(String(b.label || ''));
  });
  return tasks;
}

function getArchitectDecisionEngineerOptions(architectId) {
  var architect = state && state.agents ? state.agents[String(architectId || '')] : null;
  var group = architect ? architect.group : '';
  var engineers = _engineerEngineerAgents(group);
  return engineers.map(function(engineer) {
    var label = engineer.name || engineer.id || '';
    if (engineer.slug) label += ' (' + engineer.slug + ')';
    return { value: engineer.id, label: label };
  });
}

function _engineerDecisionSupersededByIds(architectId, decisionId) {
  var architectKey = String(architectId || '').trim();
  var decisionKey = String(decisionId || '').trim();
  var ids = [];
  var seen = {};
  if (!architectKey || !decisionKey || !state) return ids;
  var stores = [];
  if (state.decisions) stores.push(state.decisions);
  if (state.architect_decisions && state.architect_decisions !== state.decisions) {
    stores.push(state.architect_decisions);
  }
  for (var storeIndex = 0; storeIndex < stores.length; storeIndex++) {
    var store = stores[storeIndex];
    var values = Array.isArray(store) ? store : Object.keys(store).map(function(key) {
      return store[key];
    });
    for (var valueIndex = 0; valueIndex < values.length; valueIndex++) {
      var candidate = values[valueIndex] || {};
      var candidateId = String(candidate.id || '').trim();
      if (!candidateId || seen[candidateId]) continue;
      if (String(candidate.architect_id || '').trim() !== architectKey) continue;
      if (String(candidate.supersedes || '').trim() !== decisionKey) continue;
      seen[candidateId] = true;
      ids.push(candidateId);
    }
  }
  ids.sort();
  return ids;
}

function _engineerDecisionRefsHtml(ids, emptyText) {
  var values = Array.isArray(ids) ? ids.filter(function(id) {
    return String(id || '').trim();
  }) : [];
  if (!values.length) {
    return '<span class="architect-decision-ref-empty">'
      + _agentPanelEsc(emptyText || 'None') + '</span>';
  }
  var html = '<span class="architect-decision-ref-list">';
  for (var i = 0; i < values.length; i++) {
    html += '<span class="architect-decision-ref">' + _agentPanelEsc(values[i]) + '</span>';
  }
  html += '</span>';
  return html;
}

function _engineerDecisionMetaRowHtml(label, valueHtml) {
  return '<div class="detail-section-card-meta architect-decision-meta-row">'
    + '<span class="architect-decision-meta-label">' + _agentPanelEsc(label) + '</span>'
    + '<span class="architect-decision-meta-value">' + (valueHtml || '') + '</span>'
    + '</div>';
}

function _engineerDecisionTimestampSeconds(value) {
  if (typeof _agentCardTimestampSeconds === 'function') {
    return _agentCardTimestampSeconds(value);
  }
  var numeric = Number(value || 0);
  if (Number.isFinite(numeric) && numeric > 0) {
    return numeric > 100000000000 ? numeric / 1000 : numeric;
  }
  var parsed = Date.parse(String(value || ''));
  return Number.isFinite(parsed) ? parsed / 1000 : 0;
}

function _engineerDecisionTimestampIso(ts) {
  ts = _engineerDecisionTimestampSeconds(ts);
  if (!ts) return '';
  try {
    return new Date(ts * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');
  } catch (err) {
    return '';
  }
}

function _engineerDecisionRelativeTimestamp(ts) {
  ts = _engineerDecisionTimestampSeconds(ts);
  if (!ts) return '';
  var diff = Math.max(0, (Date.now() / 1000) - ts);
  if (diff < 7 * 86400 && typeof _agentPanelTimeAgo === 'function') {
    return _agentPanelTimeAgo(ts);
  }
  return _engineerDecisionTimestampIso(ts).slice(0, 10);
}

function _engineerDecisionTimestampHtml(ts, extraClass) {
  var label = _engineerDecisionRelativeTimestamp(ts);
  if (!label) return '';
  var iso = _engineerDecisionTimestampIso(ts);
  var cls = 'architect-decision-ref-empty';
  if (extraClass) cls += ' ' + extraClass;
  return '<span class="' + _agentPanelAttr(cls) + '" title="'
    + _agentPanelAttr(iso) + '">' + _agentPanelEsc(label) + '</span>';
}

function _engineerDecisionDisplayTimestamp(decision) {
  var created = _engineerDecisionTimestampSeconds(decision && decision.created_at);
  var updated = _engineerDecisionTimestampSeconds(decision && decision.updated_at);
  if (created && updated && updated > created + 60) return updated;
  return created || updated;
}

function _engineerDecisionRecencySort(a, b) {
  // Decision logs are a filing timeline: amendments update updated_at but
  // should not move existing decisions ahead of newer filed decisions.
  var aTs = _engineerDecisionTimestampSeconds(a && (a.created_at || a.updated_at));
  var bTs = _engineerDecisionTimestampSeconds(b && (b.created_at || b.updated_at));
  if (aTs !== bTs) return bTs - aTs;
  return String((a && a.id) || '').localeCompare(String((b && b.id) || ''));
}

function _agentPanelHierarchyThemeClass(levelClass, workerLevelClass) {
  var classes = String(levelClass || '') + ' ' + String(workerLevelClass || '');
  if (classes.indexOf('architect-') !== -1) return 'agent-panel-hierarchy-branch-architect';
  if (classes.indexOf('user-') !== -1 || classes.indexOf('engineer-roster-') !== -1) {
    return 'agent-panel-hierarchy-branch-user';
  }
  return 'agent-panel-hierarchy-branch-generic';
}

function _agentPanelHierarchyWorkerCount(group, engineers) {
  var count = 0;
  engineers = Array.isArray(engineers) ? engineers : [];
  if (typeof _engineerWorkerAgents !== 'function') return count;
  for (var i = 0; i < engineers.length; i++) {
    var engineer = engineers[i];
    if (!engineer) continue;
    count += _engineerWorkerAgents(group, engineer.id).length;
  }
  return count;
}

function _agentPanelHierarchyRoleLabel(role) {
  role = String(role || '').trim();
  if (role === 'architect') return 'ARCH';
  if (role === 'engineer') return 'ENGINEER';
  if (role === 'worker') return 'WORKER';
  if (role === 'user') return 'USER';
  return role;
}

function _agentPanelHierarchyAgentItem(agent, role, current) {
  role = String(role || _agentPanelKind(agent) || '').trim();
  var item = {
    role: role,
    label: _agentPanelAgentDisplayName(agent, 'Unknown ' + role),
    current: !!current,
  };
  if (agent && agent.id) item.agentId = String(agent.id);
  if (agent && agent.group) item.group = String(agent.group);
  return item;
}

function _agentPanelHierarchyUnknownItem(role) {
  role = String(role || 'agent').trim();
  return {
    role: role,
    label: 'Unknown ' + role,
    current: false,
    missing: true,
  };
}

function _agentPanelHierarchyUserItem(group, current) {
  group = String(group || '').trim();
  return {
    role: 'user',
    label: 'User',
    current: !!current,
    group: group,
    principal: 'user',
  };
}

function _agentPanelUpwardChain(agent) {
  var kind = _agentPanelKind(agent);
  var group = String((agent && agent.group) || '');
  if (kind === 'architect') {
    return [_agentPanelHierarchyAgentItem(agent, 'architect', true)];
  }
  if (kind === 'user') {
    return [_agentPanelHierarchyUserItem(group, true)];
  }
  if (kind === 'engineer') {
    var architectId = String((agent && agent.hired_by_architect_id) || '').trim();
    var items = [];
    if (architectId) {
      var architect = state && state.agents ? state.agents[architectId] : null;
      items.push(architect
        ? _agentPanelHierarchyAgentItem(architect, 'architect', false)
        : _agentPanelHierarchyUnknownItem('architect'));
    } else {
      items.push(_agentPanelHierarchyUserItem(group, false));
    }
    items.push(_agentPanelHierarchyAgentItem(agent, 'engineer', true));
    return items;
  }
  var ownerEngineerId = String(
    (agent && (agent.owner_engineer_id || agent.created_by_engineer_id)) || ''
  ).trim();
  var engineer = ownerEngineerId && state && state.agents ? state.agents[ownerEngineerId] : null;
  var chain = [];
  if (engineer) {
    var parentArchitectId = String(engineer.hired_by_architect_id || '').trim();
    if (parentArchitectId) {
      var parentArchitect = state && state.agents ? state.agents[parentArchitectId] : null;
      chain.push(parentArchitect
        ? _agentPanelHierarchyAgentItem(parentArchitect, 'architect', false)
        : _agentPanelHierarchyUnknownItem('architect'));
    } else {
      chain.push(_agentPanelHierarchyUserItem(group || engineer.group || '', false));
    }
    chain.push(_agentPanelHierarchyAgentItem(engineer, 'engineer', false));
  } else if (ownerEngineerId) {
    chain.push(_agentPanelHierarchyUnknownItem('engineer'));
  } else {
    chain.push(_agentPanelHierarchyUserItem(group, false));
  }
  chain.push(_agentPanelHierarchyAgentItem(agent, 'worker', true));
  return chain;
}

function _agentPanelUpwardBreadcrumbHtml(agent) {
  return _agentPanelHierarchyBreadcrumb(_agentPanelUpwardChain(agent));
}

function agentPanelFocusHierarchyTarget(agentId, kind, group) {
  kind = String(kind || '').trim();
  group = String(group || '').trim();
  if (kind === 'user') {
    if (typeof selectPrincipal === 'function') {
      selectPrincipal('', group);
      if (typeof renderAgentPanel === 'function') renderAgentPanel();
    } else {
      focusedItemId = 'principal:' + group + ':user';
      if (typeof renderAgentPanel === 'function') renderAgentPanel();
      else if (typeof render === 'function') render();
    }
    return;
  }

  agentId = String(agentId || '').trim();
  if (!agentId) return;
  if (typeof focusAgent === 'function') {
    focusAgent(agentId);
  } else {
    focusedItemId = agentId;
    if (typeof send === 'function') send({ cmd: 'focus_agent', id: agentId });
  }
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
  else if (typeof render === 'function') render();
}

function _agentPanelHierarchyBreadcrumb(items) {
  items = Array.isArray(items) ? items : [];
  if (!items.length) return '';
  var html = '<div class="agent-panel-hierarchy-breadcrumb" aria-label="Agent hierarchy">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i] || {};
    if (i > 0) {
      html += '<span class="agent-panel-hierarchy-arrow" aria-hidden="true">\u203A</span>';
    }
    var role = String(item.role || '').trim();
    var crumbClass = 'agent-panel-hierarchy-crumb';
    if (role) crumbClass += ' agent-panel-hierarchy-crumb-' + _agentPanelAttr(role);
    if (item.current) crumbClass += ' current';
    if (item.missing) crumbClass += ' missing';
    var clickTarget = !item.current && !item.missing
      && (String(item.agentId || '').trim() || String(item.principal || '').trim());
    var tag = clickTarget ? 'button' : 'span';
    html += '<' + tag + (clickTarget ? ' type="button"' : '') + ' class="' + crumbClass + '"';
    if (clickTarget) {
      var targetAgentId = String(item.agentId || '');
      var targetKind = String(item.principal || role || '');
      var targetGroup = String(item.group || '');
      html += ' onclick="' + _agentPanelEventAttr(
        'event.stopPropagation();agentPanelFocusHierarchyTarget('
          + JSON.stringify(targetAgentId) + ','
          + JSON.stringify(targetKind) + ','
          + JSON.stringify(targetGroup)
          + ')'
      ) + '"';
    }
    html += '>';
    if (role) {
      html += '<span class="agent-panel-hierarchy-role">' + _agentPanelEsc(_agentPanelHierarchyRoleLabel(role)) + '</span>';
    }
    html += '<span class="agent-panel-hierarchy-name">' + _agentPanelEsc(item.label || '') + '</span>';
    if (item.count !== '' && typeof item.count !== 'undefined' && item.count !== null) {
      html += '<span class="agent-panel-hierarchy-count">' + _agentPanelEsc(String(item.count)) + '</span>';
    }
    html += '</' + tag + '>';
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderWorkerRows(workers, levelClass) {
  if (!workers.length) return '';
  var html = '';
  for (var i = 0; i < workers.length; i++) {
    var worker = workers[i];
    html += '<div class="engineer-row agent-panel-hierarchy-row agent-panel-hierarchy-row-worker ' + levelClass + '">';
    html += '<div class="engineer-row-main">';
    html += '<span class="engineer-row-name">' + _esc(worker.name || worker.id || '') + '</span>';
    html += _agentPanelKindBadge('worker');
    if (worker.slug) html += '<span class="engineer-row-slug">' + _esc(worker.slug) + '</span>';
    html += '</div>';
    html += '<div class="engineer-row-meta">';
    html += '<span class="engineer-row-status engineer-row-status-' + _esc(_engineerAgentStatusLabel(worker)) + '">' + _esc(_engineerAgentStatusLabel(worker)) + '</span>';
    html += '</div></div>';
  }
  return html;
}

function _agentPanelLegacyRenderEngineerTreeRows(group, engineers, levelClass, workerLevelClass) {
  var html = '';
  var themeClass = _agentPanelHierarchyThemeClass(levelClass, workerLevelClass);
  for (var i = 0; i < engineers.length; i++) {
    var engineer = engineers[i];
    var status = _engineerEngineerStatusLabel(engineer);
    var workers = _engineerWorkerAgents(group, engineer.id);
    html += '<div class="agent-panel-hierarchy-branch ' + themeClass
      + (workers.length ? ' has-workers' : '')
      + '" data-agent-panel-hierarchy-engineer-id="' + _agentPanelAttr(engineer.id || '') + '">';
    html += '<div class="engineer-row agent-panel-hierarchy-row agent-panel-hierarchy-row-engineer ' + levelClass + '">';
    html += '<div class="engineer-row-main">';
    html += '<span class="engineer-row-name">' + _esc(engineer.name || engineer.id || '') + '</span>';
    html += _agentPanelKindBadge('engineer');
    if (engineer.slug) html += '<span class="engineer-row-slug">' + _esc(engineer.slug) + '</span>';
    html += '</div>';
    html += '<div class="engineer-row-meta">';
    html += '<span class="engineer-row-status engineer-row-status-' + _esc(status) + '">' + _esc(status) + '</span>';
    html += '</div></div>';
    if (workers.length) {
      html += '<div class="agent-panel-hierarchy-children">';
      html += _agentPanelLegacyRenderWorkerRows(workers, workerLevelClass);
      html += '</div>';
    }
    html += '</div>';
  }
  return html;
}

function _agentPanelLegacyRenderDecisionRow(architectId, decision) {
  var ui = _engineerDecisionUiState(decision.id, decision);
  var readOnly = _agentPanelIsArchitectDismissed(architectId);
  var decisionIdJs = JSON.stringify(String(decision.id || ''));
  var architectIdJs = JSON.stringify(String(architectId || ''));
  var titleFocusKey = _engineerDecisionFocusKey(decision.id, 'title');
  var rationaleFocusKey = _engineerDecisionFocusKey(decision.id, 'rationale');
  var statusFocusKey = _engineerDecisionFocusKey(decision.id, 'status');
  var linkedTaskIds = Array.isArray(decision.linked_task_ids) ? decision.linked_task_ids : [];
  var linkedEngineerIds = Array.isArray(decision.linked_engineer_ids) ? decision.linked_engineer_ids : [];
  var supersedesId = String(decision.supersedes || '').trim();
  var supersededByIds = _engineerDecisionSupersededByIds(architectId, decision.id);
  var createdTs = _engineerDecisionTimestampSeconds(decision.created_at);
  var updatedTs = _engineerDecisionTimestampSeconds(decision.updated_at);
  var headTimestampHtml = _engineerDecisionTimestampHtml(
    _engineerDecisionDisplayTimestamp(decision),
    'architect-decision-time'
  );
  var taskOptions = getArchitectDecisionTaskOptions(architectId);
  var engineerOptions = getArchitectDecisionEngineerOptions(architectId);
  var archived = _agentPanelDecisionIsArchived(decision);
  var html = '<div class="detail-section-card architect-decision-card'
    + (archived ? ' architect-decision-card-archived' : '')
    + '" data-agent-panel-anchor="decision-'
    + _agentPanelEsc(decision.id || '') + '">';
  html += '<div class="detail-section-card-head">';
  html += '<div class="architect-decision-toggle" role="button" tabindex="0" aria-expanded="'
    + (ui.expanded ? 'true' : 'false') + '" onclick="'
    + _agentPanelEventAttr('engineerToggleDecision(' + decisionIdJs + ')') + '" onkeydown="'
    + _agentPanelEventAttr('if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();engineerToggleDecision(' + decisionIdJs + ')}') + '">';
  html += '<span class="architect-decision-title-row">';
  html += '<span class="detail-section-primary architect-decision-title" title="' + _esc(decision.title || '') + '">' + _esc(decision.title || 'Decision') + '</span>';
  html += '<span class="detail-expand-caret">' + (ui.expanded ? '\u25BE' : '\u25B8') + '</span>';
  html += '</span>';
  html += '<span class="architect-decision-summary-row">';
  html += '<span class="detail-task-status">' + _esc(decision.status || 'proposed') + '</span>';
  if (archived) html += '<span class="architect-decision-archive-badge">Archived</span>';
  if (headTimestampHtml) html += headTimestampHtml;
  html += '</span>';
  html += '</div>';
  html += '<div class="detail-section-card-actions architect-decision-actions">';
  if (!readOnly && !ui.editing) {
    html += '<button type="button" class="detail-inline-editor-btn architect-decision-action-btn" title="Edit decision" aria-label="Edit decision" onclick="'
      + _agentPanelEventAttr('event.stopPropagation();engineerStartDecisionEdit(' + decisionIdJs + ')')
      + '">Edit</button>';
    if (String(decision.status || 'proposed') === 'proposed' && !decision.archived) {
      html += '<button type="button" class="detail-inline-editor-btn architect-decision-action-btn" title="Acknowledge decision" aria-label="Acknowledge decision" onclick="'
        + _agentPanelEventAttr('event.stopPropagation();engineerAcknowledgeDecision(' + architectIdJs + ',' + decisionIdJs + ')')
        + '">Ack</button>';
    }
  }
  if (!readOnly && !archived) {
    html += '<button type="button" class="detail-inline-editor-btn architect-decision-action-btn" title="Archive decision" aria-label="Archive decision" onclick="'
      + _agentPanelEventAttr('event.stopPropagation();engineerArchiveDecision(' + architectIdJs + ',' + decisionIdJs + ')')
      + '">Archive</button>';
  }
  html += '</div></div>';
  if (ui.expanded || ui.editing) {
    html += '<div class="architect-decision-body">';
    if (ui.editing && !readOnly) {
      html += '<input class="detail-inline-description-input architect-decision-input"'
        + ' id="' + _esc(titleFocusKey) + '"'
        + ' data-focus-key="' + _esc(titleFocusKey) + '"'
        + ' value="' + _esc(ui.draft.title || '') + '"'
        + ' oninput="' + _agentPanelEventAttr('engineerDecisionDraftInput(' + decisionIdJs + ',\'title\',this.value)') + '"'
        + ' placeholder="Decision title">';
      html += '<textarea class="detail-inline-description-input architect-decision-textarea" rows="4"'
        + ' id="' + _esc(rationaleFocusKey) + '"'
        + ' data-focus-key="' + _esc(rationaleFocusKey) + '"'
        + ' oninput="' + _agentPanelEventAttr('engineerDecisionDraftInput(' + decisionIdJs + ',\'rationale\',this.value)') + '"'
        + ' placeholder="Decision rationale...">' + _esc(ui.draft.rationale || '') + '</textarea>';
      html += '<select class="architect-decision-status-select"'
        + ' data-focus-key="' + _esc(statusFocusKey) + '"'
        + ' onchange="' + _agentPanelEventAttr('engineerDecisionDraftInput(' + decisionIdJs + ',\'status\',this.value)') + '">';
      for (var statusIndex = 0; statusIndex < _ENGINEER_DECISION_STATUSES.length; statusIndex++) {
        var statusOption = _ENGINEER_DECISION_STATUSES[statusIndex];
        var selected = ui.draft.status === statusOption ? ' selected' : '';
        html += '<option value="' + _esc(statusOption) + '"' + selected + '>' + _esc(statusOption) + '</option>';
      }
      html += '</select>';
      html += '<div class="detail-inline-editor-actions">';
      html += '<button type="button" class="detail-inline-editor-btn detail-inline-editor-btn-primary" onclick="'
        + _agentPanelEventAttr('engineerSaveDecisionEdit(' + architectIdJs + ',' + decisionIdJs + ')')
        + '">Save</button>';
      html += '<button type="button" class="detail-inline-editor-btn" onclick="'
        + _agentPanelEventAttr('engineerCancelDecisionEdit(' + decisionIdJs + ')')
        + '">Cancel</button>';
      html += '</div>';
    } else {
      if (decision.rationale) {
        html += '<div class="detail-section-card-body">' + _engineerMultilineHtml(decision.rationale) + '</div>';
      }
      html += _engineerDecisionMetaRowHtml(
        'Status',
        '<span class="detail-task-status">' + _agentPanelEsc(decision.status || 'proposed') + '</span>'
          + (decision.archived ? ' <span class="architect-decision-ref-empty">archived</span>' : '')
      );
      if (createdTs) {
        html += _engineerDecisionMetaRowHtml('Created', _engineerDecisionTimestampHtml(createdTs));
      }
      if (createdTs && updatedTs > createdTs + 60) {
        html += _engineerDecisionMetaRowHtml('Updated', _engineerDecisionTimestampHtml(updatedTs));
      }
      html += _engineerDecisionMetaRowHtml('Linked tasks', _engineerDecisionRefsHtml(linkedTaskIds, 'None'));
      html += _engineerDecisionMetaRowHtml('Linked engineers', _engineerDecisionRefsHtml(linkedEngineerIds, 'None'));
      if (supersedesId) {
        html += _engineerDecisionMetaRowHtml('Supersedes', _engineerDecisionRefsHtml([supersedesId], 'None'));
      }
      if (supersededByIds.length) {
        html += _engineerDecisionMetaRowHtml('Superseded by', _engineerDecisionRefsHtml(supersededByIds, 'None'));
      }
      if (!readOnly) {
        html += '<div class="architect-decision-link-row">';
        html += '<select class="architect-decision-link-select" onchange="'
          + _agentPanelEventAttr('engineerDecisionLinkSelect(' + decisionIdJs + ',\'task\',this.value)')
          + '">';
        html += '<option value="">Link task…</option>';
        for (var taskIndex = 0; taskIndex < taskOptions.length; taskIndex++) {
          var taskOption = taskOptions[taskIndex];
          var taskSelected = ui.link_task_id === taskOption.value ? ' selected' : '';
          html += '<option value="' + _esc(taskOption.value) + '"' + taskSelected + '>' + _esc(taskOption.label) + '</option>';
        }
        html += '</select>';
        html += '<button type="button" class="detail-inline-editor-btn" onclick="'
          + _agentPanelEventAttr('engineerLinkDecisionTask(' + architectIdJs + ',' + decisionIdJs + ')')
          + '">Link task</button>';
        html += '<select class="architect-decision-link-select" onchange="'
          + _agentPanelEventAttr('engineerDecisionLinkSelect(' + decisionIdJs + ',\'engineer\',this.value)')
          + '">';
        html += '<option value="">Link engineer…</option>';
        for (var engineerIndex = 0; engineerIndex < engineerOptions.length; engineerIndex++) {
          var engineerOption = engineerOptions[engineerIndex];
          var engineerSelected = ui.link_engineer_id === engineerOption.value ? ' selected' : '';
          html += '<option value="' + _esc(engineerOption.value) + '"' + engineerSelected + '>' + _esc(engineerOption.label) + '</option>';
        }
        html += '</select>';
        html += '<button type="button" class="detail-inline-editor-btn" onclick="'
          + _agentPanelEventAttr('engineerLinkDecisionEngineer(' + architectIdJs + ',' + decisionIdJs + ')')
          + '">Link engineer</button>';
        html += '</div>';
      }
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderArchitectRoster(group) {
  var architects = _engineerArchitectAgents(group);
  var html = '<section class="engineers-roster architects-roster">';
  html += '<div class="engineers-roster-header">';
  html += '<span class="engineers-roster-title">Architects</span>';
  html += '<span class="engineers-roster-count">' + architects.length + ' total</span>';
  html += '</div>';
  if (!architects.length) {
    html += '<div class="engineers-roster-empty">No architects yet. Add one to launch a dedicated architect session.</div>';
    html += '</section>';
    return html;
  }
  html += '<div class="engineers-roster-list">';
  for (var i = 0; i < architects.length; i++) {
    var architect = architects[i];
    var status = _engineerAgentStatusLabel(architect);
    var expanded = _engineerArchitectExpandedState(architect.id);
    var hireCounts = _engineerArchitectTransferCounts(architect.id);
    var architectIdJs = JSON.stringify(String(architect.id || ''));
    var dismissed = Number(architect.dismissed_at || 0) > 0;
    html += '<div class="architect-row' + (expanded ? ' expanded' : '') + '">';
    html += '<div class="engineer-row architect-parent-row">';
    html += '<div class="engineer-row-main">';
    html += '<span class="engineer-row-name">' + _esc(architect.name || architect.id || '') + '</span>';
    html += _agentPanelKindBadge('architect');
    if (architect.slug) {
      html += '<span class="engineer-row-slug">' + _esc(architect.slug) + '</span>';
    }
    html += '<span class="engineer-row-slug">decisions ' + hireCounts.decisions + ' • hired ' + hireCounts.engineers + '</span>';
    html += '</div>';
    html += '<div class="engineer-row-meta">';
    html += '<span class="engineer-row-status engineer-row-status-' + _esc(status) + '">' + _esc(status) + '</span>';
    if (dismissed) {
      html += '<button type="button" class="engineer-row-btn" onclick="event.stopPropagation();rehireArchitect(\'' + _esc(architect.id) + '\')">Rehire</button>';
    } else {
      html += '<button type="button" class="engineer-row-btn" onclick="event.stopPropagation();relaunchAgent(\'' + _esc(architect.id) + '\')">Relaunch</button>';
      html += '<button type="button" class="engineer-row-btn engineer-row-btn-danger" onclick="event.stopPropagation();dismissArchitect(\'' + _esc(architect.id) + '\')">Dismiss</button>';
    }
    html += '<button type="button" class="engineer-row-btn" onclick="event.stopPropagation();engineerRenameArchitect(\'' + _esc(architect.id) + '\')">Rename</button>';
    html += '<button type="button" class="engineer-row-btn" onclick="event.stopPropagation();engineerToggleArchitect(\'' + _esc(architect.id) + '\')">' + (expanded ? 'Hide decision log' : 'Open decision log') + '</button>';
    html += '<button type="button" class="engineer-row-btn engineer-row-btn-danger" onclick="event.stopPropagation();engineerDeleteArchitect(\'' + _esc(architect.id) + '\')">Delete</button>';
    html += '</div></div>';
    if (expanded) {
      var hiredEngineers = _engineerEngineerAgents(group, architect.id);
      var decisions = _architectDecisionsForAgent(architect.id);
      var grouped = _engineerDecisionGroups(decisions);
      html += '<div class="architect-row-body">';
      html += '<div class="architect-roster-section-head"><span class="architect-roster-section-title">Hired engineers</span><span class="architect-roster-section-count">' + hiredEngineers.length + '</span></div>';
      if (hiredEngineers.length) {
        html += '<div class="agent-panel-hierarchy-list agent-panel-hierarchy-list-architect architect-roster-subtree">';
        html += _agentPanelLegacyRenderEngineerTreeRows(
          group,
          hiredEngineers,
          'architect-roster-level-1 architect-section-engineer-row',
          'architect-roster-level-2 architect-section-worker-row'
        );
        html += '</div>';
      } else {
        html += '<div class="engineers-roster-empty architect-roster-empty">No hired engineers yet.</div>';
      }
      html += '<div class="architect-roster-section-head"><span class="architect-roster-section-title">Decision log</span><span class="architect-roster-section-actions"><span class="architect-roster-section-count">' + decisions.length + '</span>';
      if (dismissed) {
        html += '<button type="button" class="engineer-row-btn" disabled title="Rehire architect to add decisions">+ New decision</button>';
      } else {
        html += '<button type="button" class="engineer-row-btn" onclick="'
          + _agentPanelEventAttr('event.stopPropagation();openArchitectDecisionModal(' + architectIdJs + ')')
          + '">+ New decision</button>';
      }
      html += '</span></div>';
      var hasDecisionRows = false;
      for (var statusIndex = 0; statusIndex < _ENGINEER_DECISION_STATUSES.length; statusIndex++) {
        var statusName = _ENGINEER_DECISION_STATUSES[statusIndex];
        var rows = grouped[statusName] || [];
        if (!rows.length) continue;
        hasDecisionRows = true;
        html += '<div class="architect-decision-group">';
        html += '<div class="architect-decision-group-title">' + _esc(statusName) + ' <span class="architect-decision-group-count">' + rows.length + '</span></div>';
        for (var decisionIndex = 0; decisionIndex < rows.length; decisionIndex++) {
          html += _agentPanelLegacyRenderDecisionRow(architect.id, rows[decisionIndex]);
        }
        html += '</div>';
      }
      if (!hasDecisionRows) {
        html += '<div class="engineers-roster-empty architect-roster-empty">No decisions yet.</div>';
      }
      html += '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  html += '</section>';
  return html;
}

function _agentPanelLegacyRenderEngineerRoster(group) {
  var engineers = _engineerEngineerAgents(group, null);
  var html = '<section class="engineers-roster">';
  html += '<div class="engineers-roster-header">';
  html += '<span class="engineers-roster-title">Engineers</span>';
  html += '<span class="engineers-roster-count">' + engineers.length + ' user-owned</span>';
  html += '</div>';
  if (!engineers.length) {
    html += '<div class="engineers-roster-empty">No user-owned engineers yet. Add one to launch a dedicated engineer session.</div>';
    html += '</section>';
    return html;
  }
  html += '<div class="engineers-roster-list agent-panel-hierarchy-list agent-panel-hierarchy-list-user agent-panel-hierarchy-list-rooted">';
  html += '<div class="engineer-row engineer-row-virtual-parent"><div class="engineer-row-main"><span class="engineer-row-name">User</span><span class="engineer-row-kind">owner</span></div></div>';
  html += _agentPanelLegacyRenderEngineerTreeRows(
    group,
    engineers,
    'engineer-roster-level-1 user-section-engineer-row',
    'engineer-roster-level-2 user-section-worker-row'
  );
  html += '</div>';
  html += '</section>';
  return html;
}

function engineerOpenAddEngineer() {
  if (typeof openAddEngineerModal === 'function') openAddEngineerModal();
}

function engineerOpenAddArchitect() {
  if (typeof openAddArchitectModal === 'function') {
    openAddArchitectModal(_agentPanelCurrentGroup());
  }
}

function engineerRenameEngineer(engineerId) {
  var engineer = state && state.agents ? state.agents[engineerId] : null;
  if (!engineer) return;
  var currentName = String(engineer.name || '').trim();
  if (typeof showInputDialog !== 'function') return;
  return showInputDialog({
    title: 'Rename Engineer',
    fields: [
      { key: 'name', label: 'Name', defaultValue: currentName, autofocus: true },
    ],
    submitLabel: 'Rename',
  }).then(function(values) {
    if (!values || typeof values.name !== 'string') return;
    var nextName = values.name.trim();
    if (!nextName || nextName === currentName) return;
    send({ cmd: 'rename_engineer', id: engineerId, new_name: nextName });
  });
}

async function engineerDeleteEngineer(engineerId) {
  var engineer = state && state.agents ? state.agents[engineerId] : null;
  if (!engineer) return;
  var counts = _engineerEngineerTransferCounts(engineerId);
  var message = 'Deleting ' + (engineer.name || engineerId)
    + ' will transfer its ' + counts.workers + ' workers and ' + counts.tasks
    + ' tasks to the user. Continue?';
  if (await showConfirm(message, {
    label: 'Delete engineer',
    variant: 'btn-danger',
  })) {
    send({ cmd: 'delete_engineer', id: engineerId });
  }
}

function engineerRenameArchitect(architectId) {
  var architect = state && state.agents ? state.agents[architectId] : null;
  if (!architect) return;
  var currentName = String(architect.name || '').trim();
  if (typeof showInputDialog !== 'function') return;
  return showInputDialog({
    title: 'Rename Architect',
    fields: [
      { key: 'name', label: 'Name', defaultValue: currentName, autofocus: true },
    ],
    submitLabel: 'Rename',
  }).then(function(values) {
    if (!values || typeof values.name !== 'string') return;
    var nextName = values.name.trim();
    if (!nextName || nextName === currentName) return;
    send({ cmd: 'update_agent', id: architectId, name: nextName });
  });
}

async function engineerDeleteArchitect(architectId) {
  var architect = state && state.agents ? state.agents[architectId] : null;
  if (!architect) return;
  var counts = _engineerArchitectTransferCounts(architectId);
  var message = 'Deleting ' + (architect.name || architectId)
    + ' will transfer ' + counts.engineers + ' hired engineer'
    + (counts.engineers === 1 ? '' : 's')
    + ' to the user and archive ' + counts.decisions + ' decision'
    + (counts.decisions === 1 ? '' : 's') + '. Continue?';
  if (await showConfirm(message, {
    label: 'Delete architect',
    variant: 'btn-danger',
  })) {
    send({ cmd: 'delete_architect', id: architectId });
  }
}

function engineerToggleArchitect(architectId) {
  var key = String(architectId || '').trim();
  if (!key) return;
  _engineerArchitectExpanded[key] = !_engineerArchitectExpanded[key];
  _agentPanelRefreshVisibleSurface();
}

function engineerToggleDecision(decisionId) {
  var ui = _engineerDecisionUiState(decisionId);
  ui.expanded = !ui.expanded;
  _agentPanelRefreshVisibleSurface();
}

function engineerStartDecisionEdit(decisionId) {
  var architectId = _agentPanelDecisionArchitectId(decisionId);
  if (_agentPanelBlockDismissedArchitectDecisionMutation(architectId)) return;
  var ui = _engineerDecisionUiState(decisionId);
  ui.expanded = true;
  ui.editing = true;
  _agentPanelRefreshVisibleSurface();
}

function engineerDecisionDraftInput(decisionId, field, value) {
  var ui = _engineerDecisionUiState(decisionId);
  ui.draft[field] = String(value || '');
}

function engineerCancelDecisionEdit(decisionId) {
  var ui = _engineerDecisionUiState(decisionId);
  ui.editing = false;
  _agentPanelRefreshVisibleSurface();
}

function engineerSaveDecisionEdit(architectId, decisionId) {
  var ui = _engineerDecisionUiState(decisionId);
  if (_agentPanelBlockDismissedArchitectDecisionMutation(architectId)) {
    ui.editing = false;
    _agentPanelRefreshVisibleSurface();
    return;
  }
  send({
    cmd: 'architect_decision_update',
    architect_id: String(architectId || ''),
    id: String(decisionId || ''),
    title: String(ui.draft.title || '').trim(),
    rationale: String(ui.draft.rationale || '').trim(),
    status: String(ui.draft.status || 'proposed'),
  });
  ui.editing = false;
  if (typeof _showToast === 'function') _showToast('Decision updated', 'success');
  _agentPanelRefreshVisibleSurface();
}

function engineerAcknowledgeDecision(architectId, decisionId) {
  if (_agentPanelBlockDismissedArchitectDecisionMutation(architectId)) return;
  send({
    cmd: 'architect_decision_update',
    architect_id: String(architectId || ''),
    id: String(decisionId || ''),
    status: 'accepted',
  });
  if (typeof _showToast === 'function') _showToast('Decision acknowledged', 'success');
}

function engineerArchiveDecision(architectId, decisionId) {
  if (_agentPanelBlockDismissedArchitectDecisionMutation(architectId)) return;
  send({
    cmd: 'architect_decision_update',
    architect_id: String(architectId || ''),
    id: String(decisionId || ''),
    archived: true,
  });
  if (typeof _showToast === 'function') _showToast('Decision archived', 'success');
}

function engineerDecisionLinkSelect(decisionId, kind, value) {
  var ui = _engineerDecisionUiState(decisionId);
  if (kind === 'task') ui.link_task_id = String(value || '');
  if (kind === 'engineer') ui.link_engineer_id = String(value || '');
}

function engineerLinkDecisionTask(architectId, decisionId) {
  if (_agentPanelBlockDismissedArchitectDecisionMutation(architectId)) return;
  var ui = _engineerDecisionUiState(decisionId);
  if (!ui.link_task_id) return;
  send({
    cmd: 'architect_decision_link',
    architect_id: String(architectId || ''),
    id: String(decisionId || ''),
    task_id: ui.link_task_id,
  });
  ui.link_task_id = '';
  if (typeof _showToast === 'function') _showToast('Decision linked to task', 'success');
  _agentPanelRefreshVisibleSurface();
}

function engineerLinkDecisionEngineer(architectId, decisionId) {
  if (_agentPanelBlockDismissedArchitectDecisionMutation(architectId)) return;
  var ui = _engineerDecisionUiState(decisionId);
  if (!ui.link_engineer_id) return;
  send({
    cmd: 'architect_decision_link',
    architect_id: String(architectId || ''),
    id: String(decisionId || ''),
    engineer_id: ui.link_engineer_id,
  });
  ui.link_engineer_id = '';
  if (typeof _showToast === 'function') _showToast('Decision linked to engineer', 'success');
  _agentPanelRefreshVisibleSurface();
}

function renderLegacyGroupPanel() {
  var el = document.getElementById('panel-agent');
  _engineerStopEventsCountdownTimer();
  if (!el) return;
  var group = _agentPanelCurrentGroup();
  var ws = _engineerGetSettings(group);
  var activeTab = _engineerActiveTab(group);
  var legacyVirtualMetas = activeTab === 'worklog'
    ? [{
      key: _agentPanelLegacyWorklogVirtualKey(group, !!(ws && ws.restrict_to_created_agents)),
      scrollSelector: '.agent-panel-content',
    }]
    : [];
  var panelStateOptions = {
    scrollSelectors: ['.agent-panel-content'],
    captureFocusKey(active) {
      if (typeof _captureMainFocusKey === 'function') {
        var key = _captureMainFocusKey(active);
        if (key) return key;
      }
      if (active && active.classList
          && active.classList.contains('agent-panel-instructions')) {
        return '.agent-panel-instructions';
      }
      return '';
    },
    capture: function(snapshot, root) {
      if (!snapshot || !root || typeof root.querySelector !== 'function') return;
      _agentPanelCaptureVirtualScrolls(root, legacyVirtualMetas);
      snapshot.anchor = _engineerCaptureScrollAnchor(
        root.querySelector('.agent-panel-content')
      );
    },
    restore: function(root, snapshot) {
      if (!root || !snapshot || typeof root.querySelector !== 'function') return;
      _engineerRestoreScrollAnchor(
        root.querySelector('.agent-panel-content'),
        snapshot.anchor
      );
    },
  };
  var panelState = _captureSurfaceState(el, panelStateOptions);

  var engineer = group ? _engineerGetAgent(group) : null;
  var bstats = engineer
    ? _agentPanelDigestBufferStats(engineer)
    : ((state.engineer_buffer_stats && state.engineer_buffer_stats[group]) || null);
  var paused = engineer
    ? !!(_agentPanelDigestSettings(engineer) && _agentPanelDigestSettings(engineer).paused)
    : !!(ws && ws.paused);
  var emptyMessage = _engineerPanelEmptyMessage(group, ws, engineer, bstats);
  _agentPanelRenderedVirtualMetas = [];

  var html = '<div class="agent-panel-panel">';

  // Header
  html += '<div class="agent-panel-header">';
  html += '<div class="agent-panel-header-copy">';
  html += '<span class="agent-panel-title">Agent';
  if (group) html += ' — ' + _esc(group);
  html += '</span>';
  html += '<div class="agent-panel-subtitle">Architect roster, engineer hierarchy, orchestration journal, digest queue, queued assignments, completed work, and session map.</div>';
  html += '</div>';
  html += '<div class="agent-panel-header-right">';
  html += '<button type="button" class="agent-panel-add-engineer-btn" onclick="engineerOpenAddArchitect()">+ Add Architect</button>';
  html += '<button type="button" class="agent-panel-add-engineer-btn" onclick="engineerOpenAddEngineer()">+ Add Engineer</button>';
  // Buffer stats + Pause/Resume toggle
  if (!emptyMessage && group) {
    if (bstats && bstats.buffered_events > 0) {
      html += '<span class="agent-panel-buffer-stats">'
           + _esc(_engineerHeaderBufferStats(bstats, paused, engineer))
           + '</span>';
    }
    if (engineer && engineer.id) {
      html += _agentPanelDigestPauseButton(engineer);
    }
  }
  html += '</div>';
  html += '</div>';

  if (!emptyMessage) html += _agentPanelLegacyRenderTabs(group, activeTab);
  html += '<div class="agent-panel-content">';
  html += _agentPanelLegacyRenderArchitectRoster(group);
  html += _agentPanelLegacyRenderEngineerRoster(group);
  if (emptyMessage) {
    html += '<div class="agent-panel-empty">' + _esc(emptyMessage) + '</div>';
  } else if (activeTab === 'events') {
    html += _agentPanelLegacyRenderEvents(group, ws, engineer, bstats);
  } else if (activeTab === 'queued') {
    html += _agentPanelLegacyRenderQueuedTasks(group, engineer);
  } else if (activeTab === 'worklog') {
    html += _agentPanelLegacyRenderWorklog(group, ws);
  } else {
    html += _agentPanelLegacyRenderJournal(group, engineer);
  }
  html += '</div>';
  html += '</div>';
  // TORQUE:264 follow-up: memoize the legacy engineer panel clobber.
  if (el._torqueLastHtml !== html) {
    el.innerHTML = html;
    el._torqueLastHtml = html;
  }
  _restoreSurfaceState(el, panelState, panelStateOptions);
  _agentPanelAttachVirtualScrolls(el);
  _engineerSyncEventsCountdown(el, group, activeTab);
}

function engineerTogglePause() {
  engineerTogglePauseForGroup(_agentPanelCurrentGroup());
}

function engineerTogglePauseForGroup(group) {
  if (!group) return;
  var ws = _engineerGetSettings(group);
  var cmd = (ws && ws.paused) ? 'engineer_resume' : 'engineer_pause';
  send({ cmd: cmd, group: group });
}

function toggleDigestPauseForAgent(agentId) {
  agentId = String(agentId || '');
  if (!agentId) return;
  var agent = (state && state.agents && state.agents[agentId]) || { id: agentId };
  var settings = _agentPanelDigestSettings(agent);
  var cmd = (settings && settings.paused) ? 'digest_resume' : 'digest_pause';
  send({ cmd: cmd, agent_id: agentId });
}

function agentPanelTogglePauseForAgent(agentId) {
  toggleDigestPauseForAgent(agentId);
}

function engineerSelectTab(tab, group) {
  group = group || _agentPanelCurrentGroup();
  if (!group) return;
  if (tab !== 'events' && tab !== 'queued' && tab !== 'worklog') tab = 'journal';
  _engineerActiveTabByGroup[group] = tab;
  if (typeof _resolveFocusedAgent === 'function') {
    var focusedAgent = _resolveFocusedAgent();
    if (focusedAgent && _agentPanelKind(focusedAgent) === 'engineer' && typeof agentPanelSelectTab === 'function') {
      agentPanelSelectTab(tab);
      return;
    }
  }
  _agentPanelRefreshVisibleSurface();
}

function engineerSendNow() {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  send({ cmd: 'engineer_flush_now', group: group });
}

function agentPanelSendNow(agentId) {
  agentId = String(agentId || '');
  if (!agentId) return;
  send({ cmd: 'engineer_flush_now', agent_id: agentId });
}

function _engineerActiveTab(group) {
  if (!group) return 'journal';
  var tab = _engineerActiveTabByGroup[group] || 'journal';
  if (tab === 'events' || tab === 'queued' || tab === 'worklog') return tab;
  return 'journal';
}

function _engineerJournalSubview(group) {
  if (!group) return 'journal';
  return _engineerJournalSubviewByGroup[group] === 'session_map'
    ? 'session_map'
    : 'journal';
}

function _engineerSessionMapMeta(group, engineerId) {
  if (!group) return { loading: false, stale: false };
  var key = _engineerSessionMapKey(group, engineerId);
  if (!_engineerSessionMapMetaByGroup[key]) {
    _engineerSessionMapMetaByGroup[key] = { loading: false, stale: false };
  }
  return _engineerSessionMapMetaByGroup[key];
}

function _engineerFocusedSessionMapAgentId() {
  if (typeof _resolveFocusedAgent !== 'function'
      || typeof _agentPanelKind !== 'function') return '';
  var focused = _resolveFocusedAgent();
  if (!focused || _agentPanelKind(focused) !== 'engineer') return '';
  return String(focused.id || '').trim();
}

function _engineerSessionMapKey(group, engineerId) {
  group = String(group || '').trim();
  engineerId = String(
    typeof engineerId === 'undefined'
      ? _engineerFocusedSessionMapAgentId()
      : engineerId
  ).trim();
  return engineerId ? (group + '::' + engineerId) : group;
}

function _engineerDefaultSessionMapAgentId(group) {
  group = String(group || '').trim();
  if (!group || !state || !state.group_settings) return '';
  var settings = state.group_settings[group] || null;
  return String((settings && settings.engineer_agent_id) || '').trim();
}

function _engineerSessionMapData(group) {
  if (!group || !state || !state.engineer_session_maps) return null;
  var store = state.engineer_session_maps;
  var key = _engineerSessionMapKey(group);
  if (store[key]) return store[key];

  // Legacy/group-level Session Map requests do not include an engineer id, but
  // the server may answer with the group's default engineer id for strict
  // scoping. Fall back to that scoped cache entry so the group-level panel does
  // not stay stuck on the bare group key forever.
  if (!_engineerFocusedSessionMapAgentId()) {
    var defaultEngineerId = _engineerDefaultSessionMapAgentId(group);
    if (defaultEngineerId) {
      var defaultKey = _engineerSessionMapKey(group, defaultEngineerId);
      if (store[defaultKey]) return store[defaultKey];
    }
  }
  return null;
}

function _engineerRequestSessionMap(group, force) {
  if (!group) return;
  var engineerId = _engineerFocusedSessionMapAgentId();
  var meta = _engineerSessionMapMeta(group, engineerId);
  var hasData = !!_engineerSessionMapData(group);
  if (meta.loading) return;
  if (!force && hasData && !meta.stale) return;
  meta.loading = true;
  var payload = { cmd: 'engineer_session_map_read', group: group };
  if (engineerId) payload.engineer_id = engineerId;
  send(payload);
}

function _engineerResetSessionMapMeta(options) {
  options = options || {};
  var keys = Object.keys(_engineerSessionMapMetaByGroup || {});
  if (!keys.length) return;
  var clearStale = options.clearStale !== false;
  var refetchOpenMissing = !!options.refetchOpenMissing;
  var shouldRender = false;
  for (var i = 0; i < keys.length; i++) {
    var key = keys[i];
    var group = String(key || '').split('::')[0];
    if (!group) continue;
    var meta = _engineerSessionMapMetaByGroup[key];
    if (!meta) continue;
    var wasLoading = !!meta.loading;
    meta.loading = false;
    if (clearStale) meta.stale = false;
    if (refetchOpenMissing && _engineerIsSessionMapOpen(group) && !_engineerSessionMapData(group)) {
      _engineerRequestSessionMap(group, true);
      if (_engineerShouldRenderCurrentGroup(group)) shouldRender = true;
      continue;
    }
    if (wasLoading && _engineerShouldRenderCurrentGroup(group)) shouldRender = true;
  }
  if (shouldRender) _agentPanelRefreshVisibleSurface();
}

function _engineerIsSessionMapOpen(group) {
  return _engineerJournalSubview(group) === 'session_map';
}

function _engineerShouldRenderCurrentGroup(group) {
  return !!group
    && ((typeof _panelAppVisible === 'function' && _panelAppVisible('engineer'))
      || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'engineer'))
    && _agentPanelCurrentGroup() === group;
}

function _agentPanelRefreshVisibleSurface() {
  if (typeof _agentPanelRefreshCurrentTab === 'function'
      && _agentPanelRefreshCurrentTab()) {
    return;
  }
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function engineerOpenSessionMap(group) {
  group = group || _agentPanelCurrentGroup();
  if (!group) return;
  _engineerJournalSubviewByGroup[group] = 'session_map';
  _engineerRequestSessionMap(group, false);
  _agentPanelRefreshVisibleSurface();
}

function engineerCloseSessionMap(group) {
  group = group || _agentPanelCurrentGroup();
  if (!group) return;
  _engineerJournalSubviewByGroup[group] = 'journal';
  _agentPanelRefreshVisibleSurface();
}

function engineerRefreshSessionMap(group) {
  group = group || _agentPanelCurrentGroup();
  if (!group) return;
  _engineerRequestSessionMap(group, true);
  _agentPanelRefreshVisibleSurface();
}

function _engineerReceiveSessionMap(msg) {
  var group = (msg && msg.group) || '';
  if (!group) return;
  var engineerId = (msg && msg.engineer_id) || '';
  var meta = _engineerSessionMapMeta(group, engineerId);
  meta.loading = false;
  meta.stale = false;
  if (engineerId && _engineerSessionMapMetaByGroup[group]) {
    _engineerSessionMapMetaByGroup[group].loading = false;
    _engineerSessionMapMetaByGroup[group].stale = false;
  }
  if (_engineerShouldRenderCurrentGroup(group)) {
    _agentPanelRefreshVisibleSurface();
  }
}

function _engineerMarkSessionMapStale(groups) {
  if (!groups || !groups.length) return;
  var shouldRender = false;
  for (var i = 0; i < groups.length; i++) {
    var group = groups[i];
    if (!group) continue;
    var keys = Object.keys(_engineerSessionMapMetaByGroup || {}).filter(function(key) {
      return key === group || key.indexOf(group + '::') === 0;
    });
    if (!keys.length) keys = [_engineerSessionMapKey(group)];
    for (var j = 0; j < keys.length; j++) {
      var meta = _engineerSessionMapMetaByGroup[keys[j]]
        || _engineerSessionMapMeta(group);
      meta.stale = true;
    }
    if (_engineerIsSessionMapOpen(group)) {
      _engineerRequestSessionMap(group, false);
      if (_engineerShouldRenderCurrentGroup(group)) shouldRender = true;
    }
  }
  if (shouldRender) _agentPanelRefreshVisibleSurface();
}

function _agentPanelLegacyRenderTabs(group, activeTab) {
  if (!group) return '';
  var html = '<div class="agent-panel-tabs">';
  html += '<button id="engineer-tab-journal" class="agent-panel-tab'
    + (activeTab === 'journal' ? ' active' : '')
    + '" onclick="engineerSelectTab(\'journal\')">Journal</button>';
  html += '<button id="engineer-tab-events" class="agent-panel-tab'
    + (activeTab === 'events' ? ' active' : '')
    + '" onclick="engineerSelectTab(\'events\')">Events</button>';
  html += '<button id="engineer-tab-queued" class="agent-panel-tab'
    + (activeTab === 'queued' ? ' active' : '')
    + '" onclick="engineerSelectTab(\'queued\')">Queued</button>';
  html += '<button id="engineer-tab-worklog" class="agent-panel-tab'
    + (activeTab === 'worklog' ? ' active' : '')
    + '" onclick="engineerSelectTab(\'worklog\')">Completed</button>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderEvents(group, ws, engineer, bstats) {
  if (!group) {
    return '<div class="agent-panel-empty">No engineer configured for any group.</div>';
  }
  var digestSettings = engineer ? _agentPanelDigestSettings(engineer) : null;
  var paused = engineer ? !!(digestSettings && digestSettings.paused) : !!(ws && ws.paused);
  var stats = bstats || (engineer ? _agentPanelDigestBufferStats(engineer) : null);
  var sent = engineer ? _agentPanelDigestSentEvents(engineer) : [];
  if (!sent.length && state.engineer_sent_events && state.engineer_sent_events[group]) {
    sent = state.engineer_sent_events[group].slice();
  }
  return _agentPanelRenderEventsTab(
    stats,
    sent,
    paused,
    engineer,
    'engineerSendNow()',
    'Already sent to Engineer'
  );
}

function _agentPanelLegacyRenderEventSection(title, events, mode, emptyText) {
  var html = '<div class="agent-panel-event-section">';
  html += '<div class="agent-panel-event-section-header">';
  html += '<span class="agent-panel-event-section-title">' + _esc(title) + '</span>';
  html += '<span class="agent-panel-event-section-count">' + events.length + '</span>';
  html += '</div>';
  if (!events.length) {
    html += '<div class="agent-panel-event-empty">' + _esc(emptyText) + '</div>';
    html += '</div>';
    return html;
  }
  html += '<div class="agent-panel-event-list">';
  for (var i = 0; i < events.length; i++) {
    html += _agentPanelLegacyRenderEventItem(events[i], mode);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderEventItem(event, mode) {
  var kind = _engineerEventKindLabel(event && event.kind);
  var agentName = event && event.agent_name ? String(event.agent_name) : '';
  var message = event && event.message ? String(event.message) : '';
  var summary = agentName && message
    ? agentName + ' — ' + message
    : (message || agentName || kind);
  var meta = (mode === 'sent')
    ? 'sent ' + _engineerTimeAgo(event && event.delivered_at)
    : 'queued ' + _engineerTimeAgo(event && event.timestamp);
  if (mode === 'sent' && event && event.timestamp && event.delivered_at
      && Math.abs(event.delivered_at - event.timestamp) >= 30) {
    meta += ' · event ' + _engineerTimeAgo(event.timestamp);
  }
  var anchorKey = mode + '-' + String(event && event.id ? event.id : ('idx-' + meta));
  if (mode === 'sent' && event && event.delivered_at) {
    anchorKey += '-' + Math.floor(event.delivered_at);
  }

  var html = '<div class="agent-panel-event-item agent-panel-event-item-' + _esc(mode) + '"'
    + ' data-engineer-anchor="' + _esc(anchorKey) + '">';
  html += '<div class="agent-panel-event-item-header">';
  html += '<span class="agent-panel-event-kind">' + _esc(kind) + '</span>';
  html += '<span class="agent-panel-event-meta">' + _esc(meta) + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-event-message">' + _esc(summary) + '</div>';
  if (event && event.task_id) {
    html += '<div class="agent-panel-event-task">' + _esc(event.task_id) + '</div>';
  }
  html += '</div>';
  return html;
}

function _engineerHeaderBufferStats(bstats, paused, engineer) {
  if (!bstats || !bstats.buffered_events) return '';
  var evtCount = bstats.buffered_events;
  var label = evtCount + ' event' + (evtCount === 1 ? '' : 's');
  var nextPushIn = _engineerCountdownSeconds(bstats);
  if (paused) return label + ' paused';
  if (bstats.manual_flush_requested) {
    if (engineer && engineer.activity && engineer.activity !== 'waiting') {
      return label + ' queued for idle send';
    }
    return label + ' sending';
  }
  if (nextPushIn <= 0) return label + ' ready';
  return label + ' in ' + _engineerFormatCountdown(nextPushIn);
}

function _engineerEventsStatusText(bstats, paused, engineer) {
  var recipientName = String((engineer && (engineer.name || engineer.id)) || 'recipient');
  if (!bstats || !bstats.buffered_events) {
    return 'No queued events.';
  }
  if (paused) {
    return 'Delivery is paused — resume to send queued events.';
  }
  if (bstats.manual_flush_requested) {
    if (engineer && engineer.activity && engineer.activity !== 'waiting') {
      return 'Send requested — queued events will deliver when ' + recipientName + ' goes idle.';
    }
    return 'Sending queued events now.';
  }
  var nextPushIn = _engineerCountdownSeconds(bstats);
  if (nextPushIn <= 0) {
    if (engineer && engineer.activity && engineer.activity !== 'waiting') {
      return 'Eligible now — waiting for ' + recipientName + ' to go idle.';
    }
    return 'Eligible to send now.';
  }
  return 'Next eligible send in ' + _engineerFormatCountdown(nextPushIn) + '.';
}

function _engineerFormatCountdown(seconds) {
  var remaining = Math.max(0, Number(seconds) || 0);
  if (remaining < 60) return remaining + 's';
  var minutes = Math.floor(remaining / 60);
  var secs = remaining % 60;
  return minutes + 'm' + (secs > 0 ? String(secs).padStart(2, '0') + 's' : '');
}

function _engineerCountdownSeconds(bstats) {
  if (!bstats) return 0;
  var nextPushAt = Number(bstats.next_push_at || 0);
  if (nextPushAt > 0) {
    return Math.max(0, Math.ceil(nextPushAt - (Date.now() / 1000)));
  }
  return Math.max(0, Math.ceil(Number(bstats.next_push_in || 0)));
}

function _engineerShouldLiveUpdateCountdown(group, activeTab) {
  var surfaceState = _engineerVisibleEventsSurfaceState(group, activeTab);
  if (!surfaceState.group || surfaceState.tab !== 'events') return false;
  var payload = _engineerEventsSurfacePayload(surfaceState);
  if (!payload.bstats || !payload.bstats.buffered_events) return false;
  if (payload.paused) return false;
  if (payload.bstats.manual_flush_requested) return false;
  return _engineerCountdownSeconds(payload.bstats) > 0;
}

function _engineerStopEventsCountdownTimer() {
  if (_engineerEventsCountdownTimer && typeof clearInterval === 'function') {
    clearInterval(_engineerEventsCountdownTimer);
  }
  _engineerEventsCountdownTimer = 0;
}

function _engineerVisibleEventsSurfaceState(group, activeTab) {
  var fallbackGroup = String(group || '');
  var fallbackTab = String(activeTab || '');
  if (typeof _resolveFocusedAgent === 'function'
      && typeof _agentPanelKind === 'function'
      && typeof _agentPanelActiveTab === 'function') {
    var focused = _resolveFocusedAgent();
    if (focused && (
        _agentPanelKind(focused) === 'engineer'
        || _agentPanelKind(focused) === 'architect'
    )) {
      var focusedKind = _agentPanelKind(focused);
      return {
        agentId: String(focused.id || ''),
        group: String(focused.group || fallbackGroup || ''),
        tab: String(_agentPanelActiveTab(focusedKind) || fallbackTab || 'journal'),
      };
    }
  }
  var currentGroup = String(_agentPanelCurrentGroup() || fallbackGroup || '');
  return {
    agentId: '',
    group: currentGroup,
    tab: String(_engineerActiveTab(currentGroup) || fallbackTab || 'journal'),
  };
}

function _engineerEventsSurfacePayload(surfaceState) {
  var agentId = String((surfaceState && surfaceState.agentId) || '');
  var group = String((surfaceState && surfaceState.group) || '');
  if (agentId && state && state.agents && state.agents[agentId]) {
    var recipient = state.agents[agentId];
    var settings = _agentPanelDigestSettings(recipient);
    return {
      recipient: recipient,
      paused: !!(settings && settings.paused),
      bstats: _agentPanelDigestBufferStats(recipient),
    };
  }
  var ws = _engineerGetSettings(group);
  return {
    recipient: group ? _engineerGetAgent(group) : null,
    paused: !!(ws && ws.paused),
    bstats: (state.engineer_buffer_stats && state.engineer_buffer_stats[group]) || null,
  };
}

function _engineerSyncEventsCountdown(panel, group, activeTab) {
  if (!panel || typeof panel.querySelector !== 'function') return;
  var countdownEl = panel.querySelector('.agent-panel-events-countdown');
  if (!countdownEl) return;
  var surfaceState = _engineerVisibleEventsSurfaceState(group, activeTab);
  var surfaceGroup = surfaceState.group;
  var surfaceTab = surfaceState.tab;
  var payload = _engineerEventsSurfacePayload(surfaceState);
  countdownEl.textContent = _engineerEventsStatusText(
    payload.bstats,
    payload.paused,
    payload.recipient
  );
  if (!_engineerShouldLiveUpdateCountdown(surfaceGroup, surfaceTab)
      || typeof setInterval !== 'function') {
    return;
  }
  _engineerEventsCountdownTimer = setInterval(function() {
    var currentPanel = document.getElementById('panel-agent');
    if (!currentPanel) {
      _engineerStopEventsCountdownTimer();
      return;
    }
    var currentState = _engineerVisibleEventsSurfaceState(group, activeTab);
    var currentGroup = currentState.group;
    var currentTab = currentState.tab;
    var currentCountdown = currentPanel.querySelector('.agent-panel-events-countdown');
    if (!currentCountdown || currentTab !== 'events') {
      _engineerStopEventsCountdownTimer();
      return;
    }
    var currentPayload = _engineerEventsSurfacePayload(currentState);
    currentCountdown.textContent = _engineerEventsStatusText(
      currentPayload.bstats,
      currentPayload.paused,
      currentPayload.recipient
    );
    if (!_engineerShouldLiveUpdateCountdown(currentGroup, currentTab)) {
      _engineerStopEventsCountdownTimer();
    }
  }, 1000);
}

function _engineerEventKindLabel(kind) {
  kind = String(kind || '');
  if (!kind) return 'event';
  return kind.replace(/_/g, ' ');
}

function _engineerCaptureScrollAnchor(container) {
  if (!container || typeof container.querySelectorAll !== 'function'
      || typeof container.getBoundingClientRect !== 'function') {
    return null;
  }
  var items = container.querySelectorAll('[data-engineer-anchor]');
  if (!items || !items.length) return null;
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
    key: best.getAttribute ? best.getAttribute('data-engineer-anchor') : '',
    offset: anchorRect.top - containerRect.top,
  };
}

function _engineerRestoreScrollAnchor(container, snapshot) {
  if (!container || !snapshot || !snapshot.key
      || typeof container.querySelectorAll !== 'function'
      || typeof container.getBoundingClientRect !== 'function'
      || typeof container.scrollTop !== 'number') {
    return;
  }
  var items = container.querySelectorAll('[data-engineer-anchor]');
  var target = null;
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var key = item && item.getAttribute ? item.getAttribute('data-engineer-anchor') : '';
    if (key === snapshot.key) {
      target = item;
      break;
    }
  }
  if (!target || typeof target.getBoundingClientRect !== 'function') return;
  var containerRect = container.getBoundingClientRect();
  var targetRect = target.getBoundingClientRect();
  container.scrollTop += (targetRect.top - containerRect.top) - (snapshot.offset || 0);
}

// -- Worklog tab ----------------------------------------------------------

function _agentPanelLegacyRenderQueuedTasks(group, engineer) {
  if (!group) {
    return '<div class="agent-panel-empty">No engineer configured for any group.</div>';
  }
  var focusedEngineer = engineer || _engineerGetAgent(group);
  if (!focusedEngineer) {
    return '<div class="agent-panel-worklog-tab agent-panel-queued-tab">'
      + '<div class="agent-panel-worklog-header">'
      + '<span class="agent-panel-worklog-title">Queued tasks</span>'
      + '<span class="agent-panel-worklog-count">0</span>'
      + '</div>'
      + '<div class="agent-panel-event-empty">No engineer selected for this group.</div>'
      + '</div>';
  }
  return _renderEngineerQueuedTasks(focusedEngineer);
}

function _agentPanelLegacyRenderWorklog(group, ws) {
  if (!group) {
    return '<div class="agent-panel-empty">No engineer configured for any group.</div>';
  }

  var entries = (state.engineer_worklog && state.engineer_worklog[group])
    ? state.engineer_worklog[group].slice()
    : [];
  if (ws && ws.restrict_to_created_agents) {
    entries = entries.filter(function(entry) {
      return !!(entry && entry.agent_owned);
    });
  }
  entries.sort(function(a, b) {
    var startedDiff = (b.started_at || 0) - (a.started_at || 0);
    if (startedDiff) return startedDiff;
    return (b.id || 0) - (a.id || 0);
  });

  var html = '<div class="agent-panel-worklog-tab">';
  html += '<div class="agent-panel-worklog-header">';
  html += '<span class="agent-panel-worklog-title">Completed tasks</span>';
  html += '<span class="agent-panel-worklog-count">' + entries.length + '</span>';
  html += '</div>';
  if (ws && ws.restrict_to_created_agents) {
    html += '<div class="agent-panel-worklog-note">Showing completed work sent to Engineer-created agents.</div>';
  } else {
    html += '<div class="agent-panel-worklog-note">Recent completed work this Engineer dispatched in this group.</div>';
  }

  if (!entries.length) {
    html += '<div class="agent-panel-event-empty">No completed tasks yet.</div>';
    html += '</div>';
    return html;
  }

  var restricted = !!(ws && ws.restrict_to_created_agents);
  var section = restricted ? 'worklog-owned' : 'worklog-all';
  var page = _agentPanelSectionPage(group, section, entries);
  html += '<div class="agent-panel-worklog-list">';
  for (var i = 0; i < page.events.length; i++) {
    html += _agentPanelLegacyRenderWorklogItem(page.events[i]);
  }
  html += '</div>';
  html += _agentPanelRenderSectionLoadMore(page, 'task');
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderWorklogItem(entry) {
  var task = (state.board_tasks && entry && entry.task_id)
    ? state.board_tasks[entry.task_id]
    : null;
  var title = (task && task.task) || (entry && entry.task_title) || (entry && entry.task_id) || 'Task';
  var taskId = (entry && entry.task_id) || '';
  var lane = task ? (task.lane || '') : 'Not on board';
  var status = task ? String(task.status || '').trim() : '';
  var agentName = _engineerWorklogAgentLabel(entry, task);
  var meta = 'dispatched ' + _engineerTimeAgo(entry && entry.started_at);
  var anchorKey = 'worklog-' + String(entry && entry.id ? entry.id : taskId || meta);

  var html = '<div class="agent-panel-worklog-item" data-engineer-anchor="' + _esc(anchorKey) + '">';
  html += '<div class="agent-panel-worklog-item-header">';
  html += '<div class="agent-panel-worklog-task">';
  html += '<div class="agent-panel-worklog-task-title">' + _esc(title) + '</div>';
  if (taskId) {
    html += '<div class="agent-panel-worklog-task-id">' + _esc(taskId) + '</div>';
  }
  html += '</div>';
  html += '<div class="agent-panel-worklog-lane">' + _esc(lane || 'Unknown') + '</div>';
  html += '</div>';
  html += '<div class="agent-panel-worklog-meta-row">';
  html += '<span class="agent-panel-worklog-agent">' + _esc(agentName) + '</span>';
  html += '<span class="agent-panel-worklog-meta">' + _esc(meta) + '</span>';
  html += '</div>';
  if (status) {
    html += '<div class="agent-panel-worklog-status">' + _esc(status) + '</div>';
  }
  html += '</div>';
  return html;
}

function _engineerWorklogAgentLabel(entry, task) {
  var taskAgent = (task && task.agent_id && state.agents && state.agents[task.agent_id])
    ? state.agents[task.agent_id]
    : null;
  if (taskAgent) {
    return taskAgent.name || taskAgent.slug || taskAgent.id || 'Agent';
  }
  if (entry && entry.agent_name) return String(entry.agent_name);
  if (entry && entry.agent_slug) return String(entry.agent_slug);
  if (entry && entry.agent_id) return String(entry.agent_id);
  return 'Agent';
}

// -- Journal tab -----------------------------------------------------------

function _agentPanelLegacyRenderJournal(group, agent) {
  if (!group) {
    return '<div class="agent-panel-empty">No engineer configured for any group.</div>';
  }

  var html = '';
  var subview = _engineerJournalSubview(group);

  // Pending question banner
  var ws = _engineerGetSettings(group);
  if (ws && ws.pending_question) {
    html += '<div class="agent-panel-ask-banner">';
    html += '<div class="agent-panel-ask-label">Engineer is asking:</div>';
    html += '<div class="agent-panel-ask-question">' + _esc(ws.pending_question) + '</div>';
    html += '<textarea class="agent-panel-ask-reply" id="engineer-reply-input" '
         + 'placeholder="Type your reply..." rows="2" '
         + 'oninput="_engineerReplyDraft=this.value">' + _esc(_engineerReplyDraft) + '</textarea>';
    html += '<div class="agent-panel-ask-actions">';
    html += '<button class="agent-panel-dismiss-btn" onclick="engineerDismissQuestion()">Dismiss</button>';
    html += '<button class="agent-panel-reply-btn" onclick="engineerReply()">Send Reply</button>';
    html += '</div>';
    html += '</div>';
  }
  if (ws && ws.pending_note) {
    var noteKind = ws.pending_note_kind || 'note';
    html += '<div class="agent-panel-note-banner">';
    html += '<div class="agent-panel-note-label">'
      + (noteKind === 'question'
        ? 'Engineer asks (non-blocking):'
        : 'Engineer note:')
      + '</div>';
    html += '<div class="agent-panel-note-text">' + _esc(ws.pending_note) + '</div>';
    html += '<div class="agent-panel-note-actions">';
    html += '<button class="agent-panel-dismiss-btn" onclick="engineerDismissNote()">Dismiss</button>';
    html += '</div>';
    html += '</div>';
  }

  html += _agentPanelLegacyRenderJournalToolbar(group, subview);
  if (subview === 'session_map') {
    html += _agentPanelLegacyRenderSessionMap(group);
    return html;
  }

  // Journal entries come from state.engineer_journal[author_cell_id]
  // (populated by snapshots/delta ops). The legacy group-key fallback only
  // renders rows stamped with the focused/configured engineer's id.
  // Render a "last 20 + Load older" window so long sessions don't explode the
  // DOM; the shared section pager in _agentPanelSectionPage grows the window on
  // top-insert so the anchor-restore helper keeps the user's scroll position.
  var authorId = _agentPanelEngineerJournalAuthorId(group, agent);
  var entries = _agentPanelEngineerJournalEntries(group, agent);
  if (entries.length) {
    var sorted = entries.slice().sort(function(a, b) {
      return (b.id || 0) - (a.id || 0);
    });
    var page = _agentPanelSectionPage(authorId || group, 'journal', sorted);
    html += _agentPanelLegacyRenderJournalEntries(page.events, true, true);
    html += _agentPanelRenderSectionLoadMore(page, { singular: 'entry', plural: 'entries' });
  } else {
    html += '<div class="agent-panel-empty">No journal entries yet.</div>';
  }
  return html;
}

function _agentPanelLegacyRenderJournalToolbar(group, subview) {
  var groupJs = JSON.stringify(String(group || ''));
  var html = '<div class="agent-panel-session-map-toolbar">';
  html += '<div class="agent-panel-session-map-actions">';
  if (subview === 'session_map') {
    html += '<button class="agent-panel-session-map-btn" onclick=\'engineerCloseSessionMap('
      + groupJs + ")\'>Back to Journal</button>";
    html += '<button class="agent-panel-session-map-btn primary" onclick=\'engineerRefreshSessionMap('
      + groupJs + ")\'>Refresh</button>";
  } else {
    html += '<button class="agent-panel-session-map-btn primary" onclick=\'engineerOpenSessionMap('
      + groupJs + ")\'>Session Map</button>";
  }
  html += '</div>';
  var status = _engineerSessionMapStatus(group, subview);
  if (status) {
    html += '<div class="agent-panel-session-map-status">' + _esc(status) + '</div>';
  }
  html += '</div>';
  return html;
}

function _engineerSessionMapStatus(group, subview) {
  var meta = _engineerSessionMapMeta(group);
  if (subview !== 'session_map') return '';
  if (meta.loading && _engineerSessionMapData(group)) {
    return 'Refreshing deterministic snapshot…';
  }
  if (meta.loading) return 'Loading deterministic snapshot…';
  if (meta.stale) return 'Snapshot is updating…';
  return '';
}

function _agentPanelLegacyRenderSessionMap(group) {
  var sessionMap = _engineerSessionMapData(group);
  var meta = _engineerSessionMapMeta(group);
  if (!sessionMap) {
    return '<div class="agent-panel-session-map-empty">'
      + _esc(meta.loading
        ? 'Loading Session Map…'
        : 'Open Session Map to load the current deterministic orchestration snapshot.')
      + '</div>';
  }

  var html = '<div class="agent-panel-session-map">';
  html += _agentPanelLegacyRenderSessionMapOverview(sessionMap.overview || {});
  html += _agentPanelLegacyRenderSessionMapHints(sessionMap.hints || {});
  html += _agentPanelLegacyRenderSessionMapStreams(sessionMap.streams || {});
  html += _agentPanelLegacyRenderSessionMapAsks('Pending asks', sessionMap.asks || {}, 'Ask pending');
  html += _agentPanelLegacyRenderSessionMapHumanGates(sessionMap.human_gates || {});
  html += _agentPanelLegacyRenderSessionMapTaskHealth(sessionMap.task_health || {});
  html += _agentPanelLegacyRenderSessionMapVerification(sessionMap.verification || {});
  html += _agentPanelLegacyRenderSessionMapBoundaries(sessionMap.branch_boundaries || {});
  html += _agentPanelLegacyRenderSessionMapAgents(sessionMap.agents || {});
  html += _agentPanelLegacyRenderSessionMapQueuedFollowUp(sessionMap.queued_follow_up || {});
  html += _agentPanelLegacyRenderSessionMapJournal(sessionMap.journal || {});
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapOverview(overview) {
  if (!overview || typeof overview !== 'object') return '';
  var stats = [
    { label: 'Tasks', value: overview.tasks_total || 0 },
    { label: 'Streams', value: overview.active_stream_count || 0 },
    { label: 'Active agents', value: overview.active_agent_count || 0 },
    { label: 'Asks', value: overview.pending_ask_count || 0 },
    { label: 'Human gates', value: overview.human_gate_count || 0 },
    { label: 'Queued follow-up', value: overview.queued_follow_up_count || 0 },
  ];
  var html = '<div class="agent-panel-session-map-overview">';
  for (var i = 0; i < stats.length; i++) {
    html += '<div class="agent-panel-session-map-stat">';
    html += '<div class="agent-panel-session-map-stat-value">' + _esc(String(stats[i].value)) + '</div>';
    html += '<div class="agent-panel-session-map-stat-label">' + _esc(stats[i].label) + '</div>';
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapHints(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Hints</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' current hint'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-notice">'
      + _esc(_engineerHumanizeToken(item.kind || 'hint')) + '</span>';
    html += '<span class="agent-panel-session-map-item-text">' + _esc(item.message || '') + '</span>';
    html += '</div>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapStreams(summary) {
  var streams = _engineerSummaryItems(summary);
  if (!streams.length) return '';
  var html = '<div class="agent-panel-streams-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Active streams</span>';
  html += '<span class="agent-panel-health-total">' + streams.length + ' active stream'
    + (streams.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-stream-list">';
  for (var i = 0; i < streams.length; i++) {
    html += _agentPanelLegacyRenderOpenStreamCard(streams[i], i);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapAsks(title, summary, pillLabel) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">' + _esc(title) + '</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' open</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-warning">' + _esc(pillLabel) + '</span>';
    html += '<span class="agent-panel-session-map-item-text">' + _esc(item.title || '') + '</span>';
    if (item.parent_task_id) {
      html += '<span class="agent-panel-session-map-item-meta">via ' + _esc(item.parent_task_id) + '</span>';
    }
    html += '</div>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapHumanGates(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Human gates</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' waiting</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-warning">Validation gate</span>';
    html += '<span class="agent-panel-session-map-item-text">'
      + _esc(item.stream_title || item.branch || '') + '</span>';
    if (item.branch) {
      html += '<span class="agent-panel-session-map-item-meta">' + _esc(item.branch.replace(/^torque\//, '')) + '</span>';
    }
    html += '</div>';
    if (item.gate_reason) {
      html += '<div class="agent-panel-session-map-item-subtext">' + _esc(item.gate_reason) + '</div>';
    }
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapTaskHealth(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';

  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Task health</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' unhealthy</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-health-item">';
    html += '<span class="agent-panel-health-item-state agent-panel-health-pill-' + _esc(item.health_state || 'blocked') + '">'
      + _esc(_engineerHealthLabels[item.health_state] || item.health_state || 'Unhealthy') + '</span>';
    html += '<span class="agent-panel-health-item-title">' + _esc(item.title || '') + '</span>';
    if (item.via) {
      html += '<span class="agent-panel-health-item-via">via ' + _esc(item.via) + '</span>';
    }
    html += '</div>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapVerification(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-verification-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Verification</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' open checkpoint'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-verification-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(item.verification_state || 'pending') + '">'
      + _esc(_engineerVerificationLabels[item.verification_state] || item.verification_state || 'Verification') + '</span>';
    html += '<span class="agent-panel-verification-item-title">' + _esc(item.title || '') + '</span>';
    if (item.verification_mode) {
      html += '<span class="agent-panel-verification-item-meta">' + _esc(item.verification_mode) + '</span>';
    }
    html += '</div>';
    if (item.detail) {
      html += '<div class="agent-panel-verification-item-meta">' + _esc(item.detail) + '</div>';
    }
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapBoundaries(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Branch review points</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' branch'
    + (items.length === 1 ? '' : 'es') + '</span>';
  html += '</div>';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var pillState = item.partial_review_safe ? 'passed' : 'failed';
    var pillLabel = item.partial_review_safe ? 'Safe for partial review' : 'Branch advanced';
    html += '<div class="agent-panel-verification-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(pillState) + '">'
      + _esc(pillLabel) + '</span>';
    html += '<span class="agent-panel-verification-item-title">'
      + _esc(item.latest_boundary_task || item.stream_title || '') + '</span>';
    if (item.branch) {
      html += '<span class="agent-panel-verification-item-meta">' + _esc(item.branch.replace(/^torque\//, '')) + '</span>';
    }
    html += '</div>';
    var prHtml = _agentPanelRenderPrValue(_agentPanelPrMetaFromSource(item));
    if (prHtml) {
      html += '<div class="agent-panel-verification-item-meta">PR: ' + prHtml + '</div>';
    }
    if (item.foreground_task_title) {
      html += '<div class="agent-panel-verification-item-meta">Current: '
        + _esc(item.foreground_task_title) + '</div>';
    }
    if (item.queued_followups && item.queued_followups.length) {
      html += '<div class="agent-panel-verification-item-meta">Queued next: '
        + _esc(item.queued_followups.map(function(task) { return task.title; }).join(', '))
        + '</div>';
    }
    if (item.started_followups && item.started_followups.length) {
      html += '<div class="agent-panel-verification-item-meta">Beyond boundary: '
        + _esc(item.started_followups.map(function(task) { return task.title; }).join(', '))
        + '</div>';
    }
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapAgents(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Active agents</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' active</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-notice">' + _esc(item.status || 'agent') + '</span>';
    html += '<span class="agent-panel-session-map-item-text">' + _esc(item.name || item.slug || item.id || '') + '</span>';
    if (item.current_task) {
      html += '<span class="agent-panel-session-map-item-meta">' + _esc(item.current_task) + '</span>';
    }
    html += '</div>';
    if (item.activity_detail) {
      html += '<div class="agent-panel-session-map-item-subtext">' + _esc(item.activity_detail) + '</div>';
    }
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapQueuedFollowUp(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Queued follow-up</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' queued item'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var pill = item.source === 'dispatch_queue' ? 'Dispatch queue' : _engineerHumanizeToken(item.queue_state || 'Stream queue');
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-notice">' + _esc(pill) + '</span>';
    html += '<span class="agent-panel-session-map-item-text">' + _esc(item.task_title || item.task_id || '') + '</span>';
    if (item.branch) {
      html += '<span class="agent-panel-session-map-item-meta">' + _esc(item.branch.replace(/^torque\//, '')) + '</span>';
    } else if (item.target_agent_name) {
      html += '<span class="agent-panel-session-map-item-meta">' + _esc(item.target_agent_name) + '</span>';
    }
    html += '</div>';
    if (item.gate_reason) {
      html += '<div class="agent-panel-session-map-item-subtext">' + _esc(item.gate_reason) + '</div>';
    }
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapJournal(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Recent decisions, plans, and checkpoints</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' item'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += _agentPanelLegacyRenderJournalEntries(items, false);
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderJournalEntries(entries, allowContextMenu, alreadySorted) {
  var sorted = alreadySorted
    ? (entries || []).slice()
    : (entries || []).slice().sort(function(a, b) {
        return (b.id || 0) - (a.id || 0);
      });
  var html = '<div class="agent-panel-journal">';
  for (var i = 0; i < sorted.length; i++) {
    var e = sorted[i];
    var typeClass = 'agent-panel-badge-' + (e.type || 'observation');
    var ago = _engineerTimeAgo(e.timestamp);
    var anchorKey = 'journal-' + String(e.id || ('idx-' + i));
    html += '<div class="agent-panel-entry" data-engineer-anchor="' + _esc(anchorKey) + '"'
      + (allowContextMenu && e.id
        ? ' oncontextmenu="engineerEntryCtx(event,' + e.id + ')"'
        : '')
      + '>';
    html += '<div class="agent-panel-entry-header">';
    html += '<span class="agent-panel-badge ' + typeClass + '">' + _esc(e.type || '?') + '</span>';
    html += '<span class="agent-panel-entry-time">' + _esc(ago) + '</span>';
    html += '</div>';
    html += '<div class="agent-panel-entry-text">' + _esc(e.entry || '') + '</div>';
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _engineerSummaryItems(summary) {
  if (!summary || typeof summary !== 'object') return [];
  if (Array.isArray(summary.items)) return summary.items.slice();
  if (Array.isArray(summary.entries)) return summary.entries.slice();
  return [];
}

function _agentPanelLegacyRenderOpenStreams(group) {
  var summary = _engineerOpenStreamsSummary(group);
  if (!summary.show) return '';

  var html = '<div class="agent-panel-streams-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Open Streams</span>';
  html += '<span class="agent-panel-health-total">' + summary.streams.length + ' open stream'
    + (summary.streams.length === 1 ? '' : 's') + '</span>';
  html += '</div>';

  if (!summary.streams.length) {
    html += '<div class="agent-panel-stream-empty">No open streams.</div>';
    html += '</div>';
    return html;
  }

  html += '<div class="agent-panel-stream-list">';
  for (var i = 0; i < summary.streams.length; i++) {
    html += _agentPanelLegacyRenderOpenStreamCard(summary.streams[i], i);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _engineerOpenStreamsSummary(group) {
  var result = { show: false, streams: [] };
  if (!group || !state) return result;
  var raw = _engineerRawStreamPayload(group);
  if (typeof raw === 'undefined') {
    return result;
  }
  result.show = true;
  var items = _engineerStreamItemsFromPayload(raw);
  for (var i = 0; i < items.length; i++) {
    if (_engineerStreamIsOpen(items[i])) result.streams.push(items[i]);
  }
  return result;
}

function _engineerRawStreamPayload(group) {
  if (!group || !state) return undefined;
  if (state.engineer_streams
      && Object.prototype.hasOwnProperty.call(state.engineer_streams, group)) {
    return state.engineer_streams[group];
  }
  if (state.engineer_board_summary
      && state.engineer_board_summary[group]
      && state.engineer_board_summary[group].streams) {
    return state.engineer_board_summary[group].streams;
  }
  return undefined;
}

function _engineerStreamItemsFromPayload(raw) {
  if (Array.isArray(raw)) return raw.slice();
  if (!raw || typeof raw !== 'object') return [];
  if (Array.isArray(raw.items)) return raw.items.slice();
  if (Array.isArray(raw.streams)) return raw.streams.slice();
  if (raw.streams && Array.isArray(raw.streams.items)) {
    return raw.streams.items.slice();
  }
  return [];
}

function _engineerStreamIsOpen(stream) {
  if (!stream) return false;
  if (stream.is_open === false) return false;
  var mergeState = String(_engineerStreamMergeState(stream) || '').toLowerCase();
  var stateName = String(_engineerStreamStateName(stream) || '').toLowerCase();
  return mergeState !== 'merged' && stateName !== 'merged';
}

function _agentPanelLegacyRenderOpenStreamCard(stream, index) {
  var title = _engineerStreamTitle(stream);
  var branch = _engineerShortBranchLabel(_engineerStreamBranch(stream));
  var stateMeta = _engineerStreamStateMeta(stream);
  var mergeMeta = _engineerStreamMergeMeta(stream);
  var gateReason = _engineerStreamGateReason(stream);
  var nextAction = _engineerStreamActionLabel(stream);
  var latestCommit = _engineerStreamLatestReviewedCommit(stream);
  var prHtml = _agentPanelRenderPrValue(_agentPanelPrMetaFromSource(stream));
  var productTasks = _engineerStreamTaskItems(stream, 'product');
  var workflowTasks = _engineerStreamTaskItems(stream, 'workflow');
  var visibilityItems = _engineerStreamVisibilityItems(stream);
  var key = _engineerStreamAnchorKey(stream, index, title, branch);

  var html = '<div class="agent-panel-stream-card" data-engineer-anchor="' + _esc(key) + '">';
  html += '<div class="agent-panel-stream-card-header">';
  html += '<div class="agent-panel-stream-heading">';
  html += '<div class="agent-panel-stream-title-row">';
  html += '<span class="agent-panel-stream-title">' + _esc(title) + '</span>';
  html += '<span class="agent-panel-stream-state agent-panel-stream-state-'
    + _esc(stateMeta.className) + '">' + _esc(stateMeta.label) + '</span>';
  html += '</div>';
  if (branch && branch !== title) {
    html += '<div class="agent-panel-stream-branch">' + _esc(branch) + '</div>';
  }
  html += '</div>';
  if (mergeMeta.label) {
    html += '<span class="agent-panel-stream-merge agent-panel-stream-merge-' + _esc(mergeMeta.className)
      + '">' + _esc(mergeMeta.label) + '</span>';
  }
  html += '</div>';

  var metaHtml = '';
  if (latestCommit) {
    metaHtml += _agentPanelLegacyRenderStreamMetaRow('Reviewed', latestCommit);
  }
  if (prHtml) {
    metaHtml += _agentPanelLegacyRenderStreamMetaHtmlRow('PR', prHtml);
  }
  if (gateReason) {
    metaHtml += _agentPanelLegacyRenderStreamMetaRow('Gate', gateReason);
  }
  if (nextAction) {
    metaHtml += _agentPanelLegacyRenderStreamMetaRow('Next', nextAction);
  }
  if (metaHtml) {
    html += '<div class="agent-panel-stream-meta-list">';
    html += metaHtml;
    html += '</div>';
  }

  html += _agentPanelLegacyRenderMergeReadiness(stream);

  if (productTasks.length) {
    html += _agentPanelLegacyRenderStreamTaskGroup(
      'Product tasks',
      'product',
      productTasks,
      false
    );
  }
  if (workflowTasks.length) {
    html += _agentPanelLegacyRenderStreamTaskGroup(
      'Workflow',
      'workflow',
      workflowTasks,
      true
    );
  }
  if (visibilityItems.length) {
    html += _agentPanelLegacyRenderStreamVisibilityGroup(visibilityItems);
  }

  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderMergeReadiness(stream) {
  var packet = _engineerStreamMergeReadinessPacket(stream);
  if (!packet) return '';
  var presentation = _engineerMergeReadinessPresentation(stream, packet);
  var className = _engineerClassSuffix(presentation.className || 'neutral');
  var html = '<div class="agent-panel-merge-readiness agent-panel-merge-readiness-' + _esc(className) + '">';
  html += '<div class="agent-panel-merge-readiness-header">';
  html += '<span class="agent-panel-stream-section-label agent-panel-stream-section-label-merge">Merge readiness</span>';
  html += '<span class="agent-panel-merge-readiness-action agent-panel-merge-readiness-action-'
    + _esc(className) + '">' + _esc(presentation.statusLabel) + '</span>';
  html += '</div>';

  var fields = _engineerMergeReadinessFields(stream, packet);
  if (fields.length) {
    html += '<div class="agent-panel-merge-readiness-grid">';
    for (var i = 0; i < fields.length; i++) {
      html += '<div class="agent-panel-merge-readiness-label">' + _esc(fields[i].label) + '</div>';
      html += '<div class="agent-panel-merge-readiness-value">' + _esc(fields[i].value) + '</div>';
    }
    html += '</div>';
  }

  var followups = _engineerMergeReadinessFollowups(packet);
  if (followups.length) {
    html += '<div class="agent-panel-merge-readiness-followups">';
    for (var j = 0; j < followups.length; j++) {
      html += '<span class="agent-panel-merge-readiness-followup agent-panel-merge-readiness-followup-'
        + _esc(_engineerClassSuffix(followups[j].kind || 'note')) + '">'
        + _esc(followups[j].label) + '</span>';
    }
    html += '</div>';
  }

  var snippet = String(packet.merge_report_snippet || '').trim();
  if (snippet) {
    html += '<pre class="agent-panel-merge-readiness-snippet">' + _esc(snippet) + '</pre>';
  }
  html += '</div>';
  return html;
}

function _engineerStreamMergeReadinessPacket(stream) {
  if (!stream || typeof stream !== 'object') return null;
  var packet = stream.merge_readiness || stream.merge_readiness_packet || null;
  if (!packet || typeof packet !== 'object' || Array.isArray(packet)) return null;
  return packet;
}

function _engineerMergeReadinessPresentation(stream, packet) {
  var action = String((packet && packet.recommended_next_action) || (stream && stream.recommended_next_action) || '').toLowerCase();
  var streamState = String((packet && packet.state) || (stream && stream.state) || '').toLowerCase();
  var mergeState = String((packet && packet.merge_state) || (stream && stream.merge_state) || '').toLowerCase();
  var stale = !!(packet && packet.stale_base && packet.stale_base.stale === true);
  var branchAdvanced = _engineerMergeReadinessBranchAdvanced(stream, packet);
  var followups = (packet && packet.followups) || {};
  var hasBlocker = !!(followups.active_blocker_fix_task
    && (followups.active_blocker_fix_task.task_id || followups.active_blocker_fix_task.task_title));

  if (streamState === 'merged' || mergeState === 'merged'
      || (packet && packet.latest_merged_commit_sha)) {
    return { className: 'merged', statusLabel: 'Merged' };
  }
  if (stale || action === 'rebase') {
    return { className: 'stale_base', statusLabel: 'Stale base' };
  }
  if (hasBlocker || action === 'address_review_blockers' || action === 'fix_review_blocker') {
    return { className: 'blocker_fix', statusLabel: 'Blocker fix' };
  }
  if (branchAdvanced || action === 're-review' || action === 'review_latest_change') {
    return { className: 're_review', statusLabel: 'Re-review needed' };
  }
  if (streamState === 'ready_to_merge' || mergeState === 'ready'
      || action === 'merge_after_validation' || action === 'merge' || action === 'merge_stream') {
    return { className: 'ready_to_merge', statusLabel: 'Ready to merge' };
  }
  if (action === 'run_manual_validation' || streamState === 'awaiting_human_validation') {
    return { className: 'validation', statusLabel: 'Validation gate' };
  }
  if (action === 'wait_for_review' || streamState === 'reviewing') {
    return { className: 'reviewing', statusLabel: 'Awaiting review' };
  }
  return { className: 'neutral', statusLabel: 'Tracked' };
}

function _engineerMergeReadinessFields(stream, packet) {
  var fields = [];
  var action = _engineerStreamActionLabel(stream);
  if (action) fields.push({ label: 'Action', value: action });

  var boundary = (packet && packet.latest_reviewed_boundary) || {};
  var head = (packet && packet.head) || {};
  var reviewed = String(
    head.reviewed_boundary_sha
    || boundary.reviewed_sha
    || packet.latest_reviewed_commit_sha
    || ''
  ).trim();
  if (reviewed) fields.push({ label: 'Reviewed', value: _engineerShortSha(reviewed) });

  var branchHead = String(head.current_branch_head_sha || packet.branch_head || '').trim();
  if (branchHead) {
    var headValue = _engineerShortSha(branchHead);
    var headSource = String(head.current_branch_head_sha_source || '').trim();
    if (headSource && headSource !== 'unknown') {
      headValue += ' · ' + _engineerHumanizeToken(headSource);
    }
    fields.push({ label: 'Head', value: headValue });
  }

  var baseState = _engineerMergeReadinessBaseState(stream, packet);
  if (baseState) fields.push({ label: 'Base', value: baseState });

  var review = (packet && packet.review_final) || {};
  var verdict = String(review.verdict || '').trim();
  if (verdict) {
    var reviewValue = _engineerHumanizeToken(verdict);
    if (review.task_id) reviewValue += ' · ' + review.task_id;
    fields.push({ label: 'Review', value: reviewValue });
  }

  var verification = (packet && packet.verification) || {};
  var verificationState = String(
    verification.state || verification.verification_state || ''
  ).trim();
  if (verificationState) {
    var verificationValue = _engineerHumanizeToken(verificationState);
    var summary = verification.summary || {};
    if (summary.tests_run) verificationValue += ' · ' + summary.tests_run;
    else if (verification.verification_notes) verificationValue += ' · ' + verification.verification_notes;
    fields.push({ label: 'Verification', value: verificationValue });
  }

  var mergedSha = String((packet && packet.latest_merged_commit_sha) || '').trim();
  if (mergedSha) fields.push({ label: 'Merged', value: _engineerShortSha(mergedSha) });
  return fields;
}

function _engineerMergeReadinessBaseState(stream, packet) {
  var stale = (packet && packet.stale_base) || {};
  if (stale.stale === true || stale.state === 'stale') {
    var warning = String(stale.warning || stale.message || stale.merge_state || '').trim();
    return warning ? ('Stale · ' + warning) : 'Stale';
  }
  if (stale.stale === false || stale.state === 'fresh') return 'Fresh';
  if (_engineerMergeReadinessBranchAdvanced(stream, packet)) return 'Branch advanced';
  return '';
}

function _engineerMergeReadinessBranchAdvanced(stream, packet) {
  var head = (packet && packet.head) || {};
  return !!(
    (packet && packet.branch_advanced)
    || head.branch_advanced
    || (stream && stream.branch_advanced)
  );
}

function _engineerMergeReadinessFollowups(packet) {
  var followups = (packet && packet.followups) || {};
  var items = [];
  var blocker = followups.active_blocker_fix_task || {};
  if (blocker.task_id || blocker.task_title) {
    items.push({
      kind: 'blocking',
      label: 'Blocker fix: ' + (blocker.task_title || blocker.task_id),
    });
  }
  var parentReview = followups.blocker_parent_review_task || {};
  if (parentReview.task_id || parentReview.task_title) {
    items.push({
      kind: 'review',
      label: 'Review: ' + (parentReview.task_title || parentReview.task_id),
    });
  }
  var queuedCount = Number(followups.queued_count || 0);
  if (queuedCount > 0) {
    items.push({
      kind: 'queued',
      label: queuedCount + ' queued follow-up' + (queuedCount === 1 ? '' : 's'),
    });
  }
  var startedCount = Number(followups.started_count || 0);
  if (startedCount > 0) {
    items.push({
      kind: 'started',
      label: startedCount + ' started after boundary',
    });
  }
  var notes = followups.notes || {};
  ['blocking', 'non_blocking', 'future_context'].forEach(function(kind) {
    var values = Array.isArray(notes[kind]) ? notes[kind] : [];
    for (var i = 0; i < values.length && i < 2; i++) {
      var value = String(values[i] || '').trim();
      if (!value) continue;
      items.push({
        kind: kind,
        label: _engineerHumanizeToken(kind) + ': ' + value,
      });
    }
  });
  return items;
}

function _engineerShortSha(value) {
  var text = String(value || '').trim();
  if (text.length > 10) return text.slice(0, 7);
  return text;
}

function _agentPanelLegacyRenderStreamMetaRow(label, value) {
  return '<div class="agent-panel-stream-meta-label">' + _esc(label) + '</div>'
    + '<div class="agent-panel-stream-meta-value">' + _esc(value) + '</div>';
}

function _agentPanelLegacyRenderStreamMetaHtmlRow(label, htmlValue) {
  return '<div class="agent-panel-stream-meta-label">' + _esc(label) + '</div>'
    + '<div class="agent-panel-stream-meta-value">' + String(htmlValue || '') + '</div>';
}

function _agentPanelLegacyRenderStreamTaskGroup(title, kind, tasks, summarizeOnly) {
  if (!tasks.length) return '';
  var summary = tasks.length + ' ' + kind + ' task' + (tasks.length === 1 ? '' : 's');
  var html = '<div class="agent-panel-stream-task-group">';
  html += '<div class="agent-panel-stream-task-group-header">';
  html += '<span class="agent-panel-stream-section-label agent-panel-stream-section-label-' + _esc(kind)
    + '">' + _esc(title) + '</span>';
  html += '<span class="agent-panel-stream-summary-count">' + _esc(summary) + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-stream-task-list">';
  var limit = summarizeOnly ? 2 : 3;
  for (var i = 0; i < tasks.length && i < limit; i++) {
    var item = tasks[i];
    html += '<span class="agent-panel-stream-task-chip agent-panel-stream-task-chip-' + _esc(kind) + '">';
    html += _esc(item.title || item.id || '');
    html += '</span>';
  }
  if (tasks.length > limit) {
    html += '<span class="agent-panel-stream-task-chip agent-panel-stream-task-chip-more">+'
      + (tasks.length - limit) + ' more</span>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderStreamVisibilityGroup(items) {
  if (!items.length) return '';
  var html = '<div class="agent-panel-stream-task-group">';
  html += '<div class="agent-panel-stream-task-group-header">';
  html += '<span class="agent-panel-stream-section-label agent-panel-stream-section-label-context">'
    + 'Recent context</span>';
  html += '<span class="agent-panel-stream-summary-count">' + items.length + ' item'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-stream-visibility-list">';
  for (var i = 0; i < items.length && i < 2; i++) {
    var item = items[i];
    var kind = _engineerVisibilityKindLabel(item);
    html += '<div class="agent-panel-stream-visibility-item">';
    if (kind) {
      html += '<span class="agent-panel-stream-visibility-kind">' + _esc(kind) + '</span>';
    }
    html += '<span class="agent-panel-stream-visibility-text">' + _esc(item.summary) + '</span>';
    html += '</div>';
  }
  if (items.length > 2) {
    html += '<div class="agent-panel-stream-visibility-more">+' + (items.length - 2)
      + ' more context item' + (items.length - 2 === 1 ? '' : 's') + '</div>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _engineerStreamAnchorKey(stream, index, title, branch) {
  var parts = [
    stream && (stream.stream_id || stream.id || ''),
    stream && (stream.agent_id || ''),
    branch,
    title,
    String(index || 0),
  ].filter(function(part) { return !!part; });
  return 'stream-' + parts.join('-');
}

function _engineerStreamStateMeta(stream) {
  var name = _engineerStreamStateName(stream);
  var labels = {
    'implementing': 'Implementing',
    'reviewing': 'In review',
    'fixing_blockers': 'Fixing blockers',
    'awaiting_human_validation': 'Awaiting validation',
    'ready_to_merge': 'Ready to merge',
    'merged': 'Merged',
  };
  return {
    raw: name,
    label: labels[name] || _engineerHumanizeToken(name || 'implementing'),
    className: _engineerClassSuffix(name || 'implementing'),
  };
}

function _engineerStreamStateName(stream) {
  var stateName = String((stream && stream.state) || '').toLowerCase();
  var validationState = String((stream && stream.validation_state) || '').toLowerCase();
  var mergeState = String(_engineerStreamMergeState(stream) || '').toLowerCase();
  if (validationState === 'pending_human_validation') {
    return 'awaiting_human_validation';
  }
  if (stateName) return stateName;
  if (mergeState === 'ready') return 'ready_to_merge';
  return 'implementing';
}

function _engineerStreamMergeState(stream) {
  if (!stream) return '';
  if (stream.merge_state) return stream.merge_state;
  var packet = _engineerStreamMergeReadinessPacket(stream);
  if (packet) return packet.merge_state || '';
  if (typeof stream.merge_readiness === 'string') return stream.merge_readiness;
  return '';
}

function _engineerStreamMergeMeta(stream) {
  var mergeState = String(_engineerStreamMergeState(stream) || '').toLowerCase();
  var labels = {
    'ready': 'Ready to merge',
    'not_ready': 'Not ready to merge',
    'merged': 'Merged',
  };
  if (!mergeState && _engineerStreamStateName(stream) === 'ready_to_merge') {
    mergeState = 'ready';
  }
  return {
    raw: mergeState,
    label: labels[mergeState] || '',
    className: _engineerClassSuffix(mergeState || 'unknown'),
  };
}

function _engineerStreamGateReason(stream) {
  return String(
    (stream && (
      stream.gate_reason
      || (stream.queue_gate && stream.queue_gate.reason)
      || stream.gate
    )) || ''
  );
}

function _engineerStreamActionLabel(stream) {
  var packet = _engineerStreamMergeReadinessPacket(stream);
  var action = String(
    (packet && packet.recommended_next_action)
    || (stream && stream.recommended_next_action)
    || ''
  ).toLowerCase();
  var labels = {
    'continue_implementation': 'Continue implementation',
    'run_manual_validation': 'Run manual validation',
    'merge_after_validation': 'Merge after validation',
    'merge': 'Merge stream',
    'merge_stream': 'Merge stream',
    'address_review_blockers': 'Address review blockers',
    'resume_queued_work': 'Resume queued work',
    'resume_queued_task': 'Resume queued task',
    'review_latest_change': 'Review latest change',
    'wait_for_review': 'Wait for review',
    'fix_review_blocker': 'Fix review blocker',
    'rebase': 'Rebase stale base',
    're-review': 'Request re-review',
    'resolve_merge_conflict': 'Resolve merge conflict',
    'none': 'No merge action',
  };
  return labels[action] || _engineerHumanizeToken(action);
}

function _engineerStreamLatestReviewedCommit(stream) {
  var value = '';
  if (stream) {
    var packet = _engineerStreamMergeReadinessPacket(stream);
    var head = (packet && packet.head) || {};
    var boundary = (packet && packet.latest_reviewed_boundary) || {};
    if (head.reviewed_boundary_sha) value = String(head.reviewed_boundary_sha);
    else if (boundary.reviewed_sha) value = String(boundary.reviewed_sha);
    else if (stream.latest_reviewed_commit_sha) value = String(stream.latest_reviewed_commit_sha);
    else if (stream.latest_boundary_commit_sha) value = String(stream.latest_boundary_commit_sha);
    else if (stream.latest_reviewed_commit && stream.latest_reviewed_commit.sha) {
      value = String(stream.latest_reviewed_commit.sha);
    }
  }
  if (value.length > 10) return value.slice(0, 7);
  return value;
}

function _engineerStreamTaskItems(stream, kind) {
  var arrays = [];
  if (kind === 'product') {
    arrays = [
      stream && stream.product_tasks,
      stream && stream.related_product_tasks,
      stream && stream.product_task_ids,
    ];
  } else {
    arrays = [
      stream && stream.workflow_tasks,
      stream && stream.related_workflow_tasks,
      stream && stream.workflow_task_ids,
    ];
  }
  var raw = [];
  for (var i = 0; i < arrays.length; i++) {
    if (Array.isArray(arrays[i])) {
      raw = arrays[i];
      break;
    }
  }
  var items = [];
  var seen = {};
  for (var j = 0; j < raw.length; j++) {
    var item = _engineerNormalizeStreamTaskItem(raw[j]);
    if (!item.title && !item.id) continue;
    var key = item.id || item.title;
    if (seen[key]) continue;
    seen[key] = true;
    items.push(item);
  }
  return items;
}

function _engineerNormalizeStreamTaskItem(item) {
  if (typeof item === 'string' || typeof item === 'number') {
    var taskId = String(item);
    return _engineerStreamTaskFromId(taskId);
  }
  if (!item || typeof item !== 'object') return { id: '', title: '' };
  var id = item.id || item.task_id || '';
  var title = item.title || item.task || item.name || '';
  if (!title && id) {
    var resolved = _engineerStreamTaskFromId(id);
    title = resolved.title;
  }
  return {
    id: String(id || ''),
    title: String(title || ''),
  };
}

function _engineerStreamTaskFromId(taskId) {
  var task = state && state.board_tasks ? state.board_tasks[taskId] : null;
  return {
    id: String(taskId || ''),
    title: String((task && (task.task || task.title)) || taskId || ''),
  };
}

function _engineerStreamVisibilityItems(stream) {
  var raw = [];
  if (stream) {
    if (Array.isArray(stream.visibility_items)) raw = stream.visibility_items;
    else if (Array.isArray(stream.recent_visibility_items)) raw = stream.recent_visibility_items;
  }
  var items = [];
  for (var i = 0; i < raw.length; i++) {
    var item = raw[i];
    if (typeof item === 'string') {
      items.push({ kind: '', status: '', summary: item });
      continue;
    }
    if (!item || typeof item !== 'object') continue;
    var summary = item.summary || item.message || item.entry || item.title || '';
    if (!summary) continue;
    items.push({
      kind: item.kind || item.type || '',
      status: item.status || item.state || '',
      summary: String(summary),
    });
  }
  return items;
}

function _engineerVisibilityKindLabel(item) {
  if (!item) return '';
  var status = String(item.status || '').toLowerCase();
  var kind = String(item.kind || '').toLowerCase();
  if (status) return _engineerHumanizeToken(status);
  if (kind) return _engineerHumanizeToken(kind);
  return 'Note';
}

function _engineerStreamTitle(stream) {
  var title = '';
  if (stream) {
    title = stream.short_label
      || stream.friendly_title
      || stream.foreground_task_title
      || stream.display_name
      || stream.label
      || stream.title
      || '';
  }
  if (!title) {
    var productTasks = _engineerStreamTaskItems(stream, 'product');
    if (productTasks.length) title = productTasks[0].title || '';
  }
  if (!title && stream && stream.latest_boundary_task_title) {
    title = stream.latest_boundary_task_title;
  }
  if (title) return String(title);
  return _engineerShortBranchLabel(_engineerStreamBranch(stream)) || 'Untitled stream';
}

function _engineerStreamBranch(stream) {
  return String((stream && (stream.branch || stream.worktree_branch || stream.stream_branch)) || '');
}

function _engineerShortBranchLabel(branch) {
  return String(branch || '').replace(/^torque\//, '');
}

function _engineerHumanizeToken(value) {
  var text = String(value || '').trim();
  if (!text) return '';
  text = text.replace(/[_-]+/g, ' ');
  return text.replace(/\b([a-z])/g, function(match, chr) {
    return chr.toUpperCase();
  });
}

function _engineerClassSuffix(value) {
  return String(value || 'unknown').toLowerCase().replace(/[^a-z0-9_-]+/g, '-');
}

function _agentPanelLegacyRenderTaskHealth(group) {
  var summary = _engineerTaskHealthSummary(group);
  if (!summary.total) return '';

  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Task health</span>';
  html += '<span class="agent-panel-health-total">' + summary.total + ' unhealthy</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-counts">';
  for (var i = 0; i < _engineerHealthOrder.length; i++) {
    var stateName = _engineerHealthOrder[i];
    var count = summary.counts[stateName] || 0;
    if (!count) continue;
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(stateName) + '">'
      + count + ' ' + _esc(_engineerHealthLabels[stateName]) + '</span>';
  }
  html += '</div>';
  if (summary.items.length) {
    html += '<div class="agent-panel-health-list">';
    for (var j = 0; j < summary.items.length; j++) {
      var item = summary.items[j];
      html += '<div class="agent-panel-health-item">';
      html += '<span class="agent-panel-health-item-state agent-panel-health-pill-' + _esc(item.health_state) + '">'
        + _esc(_engineerHealthLabels[item.health_state] || item.health_state) + '</span>';
      html += '<span class="agent-panel-health-item-title">' + _esc(item.title) + '</span>';
      if (item.via) {
        html += '<span class="agent-panel-health-item-via">via ' + _esc(item.via) + '</span>';
      }
      html += '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderVerificationSummary(group) {
  var summary = _engineerVerificationSummary(group);
  if (!summary.total) return '';

  var html = '<div class="agent-panel-verification-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Verification</span>';
  html += '<span class="agent-panel-health-total">' + summary.total + ' open checkpoint' + (summary.total === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-counts">';
  for (var i = 0; i < summary.order.length; i++) {
    var stateName = summary.order[i];
    var count = summary.counts[stateName] || 0;
    if (!count) continue;
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(stateName) + '">'
      + count + ' ' + _esc(_engineerVerificationLabels[stateName] || stateName) + '</span>';
  }
  html += '</div>';
  for (var j = 0; j < summary.items.length; j++) {
    var item = summary.items[j];
    html += '<div class="agent-panel-verification-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(item.verification_state) + '">'
      + _esc(_engineerVerificationLabels[item.verification_state] || item.verification_state) + '</span>';
    html += '<span class="agent-panel-verification-item-title">' + _esc(item.title) + '</span>';
    if (item.verification_mode) {
      html += '<span class="agent-panel-verification-item-meta">' + _esc(item.verification_mode) + '</span>';
    }
    html += '</div>';
    if (item.detail) {
      html += '<div class="agent-panel-verification-item-meta">' + _esc(item.detail) + '</div>';
    }
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderBoundarySummary(group) {
  if (!group || !state || !state.agents) return '';
  var items = [];
  var seen = {};
  for (var agentId in state.agents) {
    var agent = state.agents[agentId];
    if (!agent || agent.cell_type !== 'agent' || agent.group !== group) continue;
    var settings = (state.group_settings || {})[group] || {};
    if (settings.engineer_agent_id === agent.id) continue;
    var overview = typeof _branchBoundaryOverviewForAgent === 'function'
      ? _branchBoundaryOverviewForAgent(agent)
      : null;
    if (!overview || !overview.latest_boundary_task) continue;
    var key = (overview.repo_root || '') + '::' + (overview.branch || '');
    if (seen[key]) continue;
    seen[key] = true;
    items.push({
      agent_name: agent.name,
      branch: overview.branch || '',
      current_task: overview.current_task ? overview.current_task.task : '',
      latest_boundary_task: overview.latest_boundary_task.task || '',
      pr: (typeof _worktreePrMetadataFromBoundary === 'function')
        ? _worktreePrMetadataFromBoundary(
            (typeof _taskBoundaryMeta === 'function')
              ? _taskBoundaryMeta(overview.latest_boundary_task)
              : ((overview.latest_boundary_task && overview.latest_boundary_task.worktree_boundary) || {})
          )
        : {},
      queued_followers: overview.queued_followers || [],
      started_followers: overview.started_followers || [],
      partial_review_safe: !!overview.partial_review_safe,
    });
  }
  items.sort(function(a, b) {
    return (a.branch || a.agent_name || '').localeCompare(
      b.branch || b.agent_name || ''
    );
  });
  if (!items.length) return '';

  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Branch review points</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' branch'
    + (items.length === 1 ? '' : 'es') + '</span>';
  html += '</div>';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var pillState = item.partial_review_safe ? 'passed' : 'failed';
    var pillLabel = item.partial_review_safe ? 'Safe for partial review' : 'Branch advanced';
    html += '<div class="agent-panel-verification-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(pillState) + '">'
      + _esc(pillLabel) + '</span>';
    html += '<span class="agent-panel-verification-item-title">' + _esc(item.latest_boundary_task) + '</span>';
    if (item.branch) {
      html += '<span class="agent-panel-verification-item-meta">' + _esc(item.branch.replace(/^torque\//, '')) + '</span>';
    }
    html += '</div>';
    var prHtml = _agentPanelRenderPrValue(_agentPanelPrMetaFromSource(item));
    if (prHtml) {
      html += '<div class="agent-panel-verification-item-meta">PR: ' + prHtml + '</div>';
    }
    if (item.current_task) {
      html += '<div class="agent-panel-verification-item-meta">Current: ' + _esc(item.current_task) + '</div>';
    }
    if (item.queued_followers.length) {
      html += '<div class="agent-panel-verification-item-meta">Queued next: '
        + _esc(item.queued_followers.map(function(task) { return task.task; }).join(', '))
        + '</div>';
    }
    if (item.started_followers.length) {
      html += '<div class="agent-panel-verification-item-meta">Beyond boundary: '
        + _esc(item.started_followers.map(function(task) { return task.task; }).join(', '))
        + '</div>';
    }
  }
  html += '</div>';
  return html;
}

function _engineerTaskHealthSummary(group) {
  var summary = {
    counts: { 'blocked': 0, 'stalled': 0, 'thrashing': 0, 'idle-risk': 0 },
    items: [],
    total: 0,
  };
  var tasks = (state && state.board_tasks) || {};
  for (var id in tasks) {
    var task = tasks[id];
    if (task.group !== group || task.lane === 'Done') continue;
    var healthState = task.health_state || 'healthy';
    if (healthState === 'healthy') continue;
    summary.counts[healthState] = (summary.counts[healthState] || 0) + 1;
    summary.total += 1;
    var details = task.health_details || {};
    summary.items.push({
      id: task.id,
      title: task.task || '',
      health_state: healthState,
      health_since: task.health_since || '',
      via: details.aggregate ? (details.source_task_title || '') : '',
    });
  }
  summary.items.sort(function(a, b) {
    var sev = (_engineerHealthSeverity[b.health_state] || 0) - (_engineerHealthSeverity[a.health_state] || 0);
    if (sev) return sev;
    var timeCmp = (a.health_since || '').localeCompare(b.health_since || '');
    if (timeCmp) return timeCmp;
    return (a.title || '').localeCompare(b.title || '');
  });
  summary.items = summary.items.slice(0, 5);
  return summary;
}

function _engineerVerificationSummary(group) {
  if (typeof _compactHydrateTasksMatching === 'function') {
    _compactHydrateTasksMatching(function(task) {
      if (!task || task.group !== group || task.lane === 'Done') return false;
      return !!(task.verification_state || task.verification_mode);
    });
  }
  var summary = {
    counts: { pending: 0, attempted: 0, passed: 0, failed: 0 },
    order: ['failed', 'pending', 'attempted', 'passed'],
    items: [],
    total: 0,
  };
  var tasks = (state && state.board_tasks) || {};
  for (var id in tasks) {
    var task = tasks[id];
    if (task.group !== group || task.lane === 'Done') continue;
    var verificationState = task.verification_state || '';
    if (!verificationState || !summary.counts.hasOwnProperty(verificationState)) continue;
    summary.counts[verificationState] += 1;
    summary.total += 1;
    var verificationSummary = task.verification_summary || {};
    summary.items.push({
      id: task.id,
      title: task.task || '',
      verification_state: verificationState,
      verification_mode: task.verification_mode || '',
      detail: verificationSummary.human_validation_pending
        || task.verification_notes
        || verificationSummary.tests_run
        || '',
    });
  }
  summary.items.sort(function(a, b) {
    var aRank = summary.order.indexOf(a.verification_state);
    var bRank = summary.order.indexOf(b.verification_state);
    if (aRank !== bRank) return aRank - bRank;
    return (a.title || '').localeCompare(b.title || '');
  });
  summary.items = summary.items.slice(0, 5);
  return summary;
}

// -- Journal context menu --------------------------------------------------

function engineerEntryCtx(e, entryId) {
  e.preventDefault();
  e.stopPropagation();
  showContextMenu(e.clientX, e.clientY, [
    { label: 'Delete entry', danger: true, action: 'engineerDeleteEntry(' + entryId + ')' },
  ]);
}

function engineerDeleteEntry(entryId) {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  var focused = _resolveFocusedAgent();
  var authorId = _agentPanelEngineerJournalAuthorId(group, focused);
  send({
    cmd: 'engineer_journal_delete',
    group: group,
    entry_id: entryId,
    author_cell_id: authorId,
  });
  // Optimistic removal from local state
  if (state.engineer_journal && authorId && state.engineer_journal[authorId]) {
    state.engineer_journal[authorId] = state.engineer_journal[authorId].filter(
      function(e) { return e.id !== entryId; });
  } else if (state.engineer_journal && state.engineer_journal[group]) {
    state.engineer_journal[group] = state.engineer_journal[group].filter(
      function(e) { return e.id !== entryId; });
  }
  _agentPanelRefreshVisibleSurface();
}

// -- Human reply -----------------------------------------------------------

function engineerReply() {
  var input = document.getElementById('engineer-reply-input');
  if (!input) return;
  var answer = input.value.trim();
  if (!answer) return;
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  _engineerReplyDraft = '';
  // Blur so the re-render skip guard doesn't block the banner clearing
  input.blur();
  send({ cmd: 'engineer_reply', group: group, answer: answer });
}

function engineerDismissQuestion() {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  _engineerReplyDraft = '';
  send({ cmd: 'engineer_resume', group: group });
}

function engineerDismissNote() {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  send({ cmd: 'engineer_dismiss_note', group: group });
}

// -- Event handlers --------------------------------------------------------

function engineerInstrInput(textarea) {
  _engineerCustomInstrDirty = true;
  _engineerCustomInstrDraft = textarea.value;
  // Show save button (re-render just the settings section would be heavy;
  // instead just toggle the button visibility)
  var btn = textarea.parentElement.querySelector('.agent-panel-save-btn');
  if (!btn) {
    var b = document.createElement('button');
    b.className = 'agent-panel-save-btn';
    b.textContent = 'Save';
    b.onclick = engineerSaveInstructions;
    textarea.parentElement.appendChild(b);
  }
}

function engineerSaveInstructions() {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  send({
    cmd: 'engineer_update_settings',
    group: group,
    custom_instructions: _engineerCustomInstrDraft,
  });
  _engineerCustomInstrDirty = false;
  _engineerCustomInstrDraft = '';
}

function engineerUpdateSetting(key, value) {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  var payload = { cmd: 'engineer_update_settings', group: group };
  payload[key] = value;
  send(payload);
}

function engineerToggleEvent(evt, enabled) {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  var ws = _engineerGetSettings(group);
  var current = (ws && ws.enabled_events) ? ws.enabled_events.slice() : [];
  if (enabled && current.indexOf(evt) < 0) {
    current.push(evt);
  } else if (!enabled) {
    current = current.filter(function(e) { return e !== evt; });
  }
  send({
    cmd: 'engineer_update_settings',
    group: group,
    enabled_events: current,
  });
}

// -- Helpers ---------------------------------------------------------------

function _engineerGetSettings(group) {
  if (!group || !state.engineer_settings) return null;
  return state.engineer_settings[group] || null;
}

function _engineerPanelEmptyMessage(group, ws, engineer, bstats) {
  if (!group) return 'Select a group to inspect Engineer orchestration state.';
  if (_engineerHasPanelState(group, ws, engineer, bstats)) return '';
  return 'No Engineer configured for ' + group + ' yet.';
}

function _engineerHasPanelState(group, ws, engineer, bstats) {
  if (!group) return false;
  if (engineer) return true;
  if (_engineerGroupHasConfiguredAgent(group)) return true;
  if (_engineerGroupHasState(ws)) return true;
  if (_engineerGroupHasState(bstats)) return true;
  if (_engineerGroupStoreHasState(state.engineer_board_summary, group)) return true;
  if (_engineerGroupStoreHasState(state.engineer_buffer_stats, group)) return true;
  if (_engineerJournalStoreHasStateForGroup(state.engineer_journal, group)) return true;
  if (_engineerGroupStoreHasState(state.engineer_sent_events, group)) return true;
  if (_engineerGroupStoreHasState(state.engineer_session_maps, group)) return true;
  if (_engineerGroupStoreHasState(state.engineer_streams, group)) return true;
  if (_engineerGroupStoreHasState(state.engineer_worklog, group)) return true;
  return false;
}

function _engineerGroupHasConfiguredAgent(group) {
  if (!group || !state || !state.group_settings) return false;
  var settings = state.group_settings[group];
  return !!(settings && settings.engineer_agent_id);
}

function _engineerGroupStoreHasState(store, group) {
  if (!store || !group) return false;
  if (_engineerGroupHasState(store[group])) return true;
  var prefix = String(group || '') + '::';
  for (var key in store) {
    if (key.indexOf(prefix) === 0 && _engineerGroupHasState(store[key])) {
      return true;
    }
  }
  return _engineerGroupHasState(store[group]);
}

function _engineerJournalStoreHasStateForGroup(store, group) {
  if (!store || !group) return false;
  var configured = _agentPanelEngineerAgent(group);
  if (configured && configured.id && _engineerGroupHasState(store[configured.id])) {
    return true;
  }
  if (state && state.agents) {
    for (var aid in state.agents) {
      var agent = state.agents[aid];
      if (!agent || String(agent.group || '') !== String(group || '')) continue;
      if (String(agent.kind || '') !== 'engineer') continue;
      if (_engineerGroupHasState(store[aid])) return true;
    }
  }
  for (var key in store) {
    var entries = store[key];
    if (!Array.isArray(entries)) continue;
    for (var i = 0; i < entries.length; i++) {
      if (String((entries[i] && entries[i].group) || '') === String(group || '')) {
        return true;
      }
    }
  }
  return false;
}

function _engineerGroupHasState(value) {
  if (value == null) return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value).length > 0;
  return !!value;
}

function _agentPanelCurrentGroup() {
  if (typeof _singleGroupModeEnabled === 'function'
      && _singleGroupModeEnabled()
      && typeof _activeGroup === 'function') {
    return _activeGroup() || '';
  }
  if (typeof _resolveFocusedAgent === 'function') {
    var focusedAgent = _resolveFocusedAgent();
    if (focusedAgent && focusedAgent.group) return focusedAgent.group || '';
  }
  if (typeof _focusedGroup === 'function') {
    var focused = _focusedGroup();
    if (focused) return focused;
  }
  if (state && state.active_session_id && state.agents) {
    for (var agentId in state.agents) {
      var agent = state.agents[agentId];
      if (agent && agent.session_id === state.active_session_id) {
        return agent.group || '';
      }
    }
  }
  if (typeof _currentGroup === 'function') {
    var group = _currentGroup();
    if (group) return group;
  }
  if (state && state.groups) {
    var groups = Object.keys(state.groups || {});
    if (groups.length) return groups[0];
  }
  return '';
}

function _engineerGetAgent(group) {
  if (!group || !state.group_settings) return null;
  var gs = state.group_settings[group];
  if (!gs || !gs.engineer_agent_id) return null;
  return state.agents ? state.agents[gs.engineer_agent_id] : null;
}

function _engineerTimeAgo(ts) {
  if (!ts) return '';
  var diff = (Date.now() / 1000) - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function _esc(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;')
          .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

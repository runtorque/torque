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

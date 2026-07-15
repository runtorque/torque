/* Agent panel module: events. */

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
  html += '<div class="agent-panel-events-toolbar ui-toolbar ui-toolbar--bordered">';
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
  var html = '<div class="ui-tabs--contained agent-panel-events-subtabs" role="tablist" aria-label="Events views">';
  for (var i = 0; i < tabs.length; i++) {
    var tab = tabs[i];
    html += '<button type="button"'
      + ' id="agent-panel-events-subtab-' + _agentPanelEsc(tab.key) + '"'
      + ' class="ui-tab ui-tab--contained agent-panel-events-subtab' + (active === tab.key ? ' active' : '') + '"'
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
  html += '<span class="agent-panel-event-section-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">'
    + _agentPanelEventSectionCount(page) + '</span>';
  html += '</div>';
  if (!total) {
    html += '<div class="agent-panel-event-empty ui-state ui-state--empty ui-state--compact">' + _esc(emptyText) + '</div>';
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
  html += '<div class="agent-panel-mcp-filters form-control-group-sm">';
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
    html += '<div class="agent-panel-event-empty ui-state ui-state--loading ui-state--compact" role="status" aria-live="polite">Loading MCP calls…</div>';
  } else if (!calls.length) {
    html += '<div class="agent-panel-event-empty ui-state ui-state--empty ui-state--compact">No MCP calls found.</div>';
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

function _renderAgentDigestEvents(agent) {
  var group = String((agent && agent.group) || '');
  var bstats = _agentPanelDigestBufferStats(agent);
  var sentEvents = _agentPanelDigestSentEvents(agent);
  var settings = _agentPanelDigestSettings(agent);
  var paused = !!(settings && settings.paused);
  if (!group) return '<div class="agent-panel-empty ui-state ui-state--empty ui-state--compact">No group events yet.</div>';
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
  html += '<span class="agent-panel-event-section-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">' + _agentPanelEventSectionCount(page) + '</span>';
  html += '</div>';
  if (loading && !events.length) {
    html += '<div class="agent-panel-event-empty ui-state ui-state--loading ui-state--compact" role="status" aria-live="polite">Loading cell events…</div>';
    html += '</div>';
    return html;
  }
  if (!events.length) {
    html += '<div class="agent-panel-event-empty ui-state ui-state--empty ui-state--compact">No cell events yet.</div>';
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

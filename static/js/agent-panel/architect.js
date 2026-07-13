/* Agent panel module: architect. */

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

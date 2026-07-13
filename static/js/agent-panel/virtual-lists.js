/* Agent panel module: virtual-lists. */

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

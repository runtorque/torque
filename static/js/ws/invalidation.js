/* Delta surface invalidation and render batching. */

function _statusBarDeltaNeedsRefresh(ops, hints) {
  for (let i = 0; i < (ops || []).length; i++) {
    const op = ops[i] || {};
    switch (op.op) {
      case 'agent_upsert':
      case 'agent_remove':
      case 'task_upsert':
      case 'task_remove':
        return true;
      case 'global_settings_update':
        if (!_globalSettingsDeltaHasChangedKeys(op)
            || _globalSettingsDeltaChangedKeys(op).indexOf('status_bar_visibility') >= 0) {
          return true;
        }
        break;
      case 'ui_update':
        if (op.key === 'active_group'
            || op.key === 'events_dismissed_attention'
            || op.key === 'selected_agent_id') {
          return true;
        }
        break;
    }
  }
  return false;
}

function _standaloneDeltaOptimizationsEnabled() {
  return !!(
    typeof _standalonePanelsEnabled === 'function'
    && _standalonePanelsEnabled()
  );
}

function _surfaceInvalidationsAny(flags) {
  if (!flags) return false;
  for (const key in flags) {
    if (key && flags[key]) return true;
  }
  return false;
}

function _mergeSurfaceInvalidations(target, source) {
  const out = target || _blankSurfaceInvalidations();
  for (const key in (source || {})) {
    if (source[key]) out[key] = true;
    else if (!Object.prototype.hasOwnProperty.call(out, key)) out[key] = false;
  }
  return out;
}

function _renderDeltaSurfaceInvalidations(flags) {
  if (!_surfaceInvalidationsAny(flags)) return;
  var _renderStart = (typeof performance !== 'undefined' && performance && typeof performance.now === 'function')
    ? performance.now()
    : (Date.now ? Date.now() : 0);
  try {
    if (typeof renderInvalidatedSurfaces === 'function') {
      renderInvalidatedSurfaces(flags);
    } else {
      render();
    }
  } finally {
    if (typeof healthRecordFrontendRender === 'function') {
      var _renderEnd = (typeof performance !== 'undefined' && performance && typeof performance.now === 'function')
        ? performance.now()
        : (Date.now ? Date.now() : _renderStart);
      healthRecordFrontendRender(Math.max(0, _renderEnd - _renderStart), 'surface-delta');
    }
  }
}

function _cancelPendingDeltaSurfaceRender() {
  if (_pendingDeltaSurfaceRenderFrame
      && typeof cancelAnimationFrame === 'function') {
    cancelAnimationFrame(_pendingDeltaSurfaceRenderFrame);
  }
  _pendingDeltaSurfaceRenderFrame = 0;
  _pendingDeltaSurfaceInvalidations = null;
}

function _flushDeltaSurfaceRenderBatch() {
  if (!_pendingDeltaSurfaceInvalidations) return;
  // If a press is in progress (e.g. an rAF was scheduled before the user
  // pressed), keep the batch queued and re-arm the rAF after release.
  // Otherwise the rAF would replace the DOM mid-press and suppress click.
  // TORQUE:264 follow-up: same gate also applies to active hover on
  // tooltip-keyed surfaces (see `_userHovering`).
  if (_userInteracting()) {
    _pendingDeltaSurfaceRenderFrame = 0;
    return;
  }
  const flags = _pendingDeltaSurfaceInvalidations;
  _pendingDeltaSurfaceInvalidations = null;
  _pendingDeltaSurfaceRenderFrame = 0;
  if (!dragInProgress) _renderDeltaSurfaceInvalidations(flags);
}

function _queueDeltaSurfaceRender(flags) {
  if (!_surfaceInvalidationsAny(flags)) return;
  // Always coalesce when rAF is available — toolbelt mode used to bypass
  // this path and render synchronously per delta, which made the
  // worker-MCP firehose (:224) freely interleave DOM swaps with user
  // presses. The ~16 ms of latency is invisible compared to losing every
  // click.
  if (typeof requestAnimationFrame !== 'function') {
    if (_userInteracting()) {
      _pendingDeltaSurfaceInvalidations = _mergeSurfaceInvalidations(
        _pendingDeltaSurfaceInvalidations,
        flags
      );
      return;
    }
    _renderDeltaSurfaceInvalidations(flags);
    return;
  }
  _pendingDeltaSurfaceInvalidations = _mergeSurfaceInvalidations(
    _pendingDeltaSurfaceInvalidations,
    flags
  );
  if (_userInteracting()) return;
  if (_pendingDeltaSurfaceRenderFrame) return;
  _pendingDeltaSurfaceRenderFrame = requestAnimationFrame(function() {
    _flushDeltaSurfaceRenderBatch();
  });
}

function _blankSurfaceInvalidations() {
  return {
    main: false,
    board: false,
    context: false,
    events: false,
    engineer: false,
    templates: false,
    health: false,
    thinking: false,
    terminal: false,
    // Not a panel surface: routes the status-bar refresh through the same
    // rAF batch so it stops rebuilding synchronously per WS message.
    statusbar: false,
  };
}

function _markSurface(flags) {
  for (let i = 1; i < arguments.length; i++) {
    flags[arguments[i]] = true;
  }
}

function _contextDeltaAgentId(op) {
  return String(
    (op && (op.cell_id || op.agent_id || op.id))
      || ''
  ).trim();
}

function _contextWindowPayloadFromOp(op) {
  if (!op || typeof op !== 'object') return undefined;
  if (Object.prototype.hasOwnProperty.call(op, 'context_window')) {
    return op.context_window;
  }
  const data = op.data && typeof op.data === 'object' ? op.data : null;
  if (data && Object.prototype.hasOwnProperty.call(data, 'context_window')) {
    return data.context_window;
  }
  return undefined;
}

function _providerUsagePayloadFromOp(op) {
  if (!op || typeof op !== 'object') return undefined;
  if (Object.prototype.hasOwnProperty.call(op, 'provider_usage')) {
    return op.provider_usage;
  }
  const data = op.data && typeof op.data === 'object' ? op.data : null;
  if (data && Object.prototype.hasOwnProperty.call(data, 'provider_usage')) {
    return data.provider_usage;
  }
  return undefined;
}

function _applyContextMeterDeltaUpdates(agentIds) {
  if (!Array.isArray(agentIds) || !agentIds.length) return;
  if (typeof updateAgentContextMeter !== 'function') return;
  const seen = {};
  for (let i = 0; i < agentIds.length; i++) {
    const id = String(agentIds[i] || '').trim();
    if (!id || seen[id]) continue;
    seen[id] = true;
    updateAgentContextMeter(id);
  }
}

function _collectContextMeterDeltaAgentIds(ops, hints) {
  const ids = [];
  const add = function(value) {
    value = String(value || '').trim();
    if (value && ids.indexOf(value) < 0) ids.push(value);
  };
  for (let i = 0; i < (ops || []).length; i++) {
    const op = ops[i] || {};
    if (op.op === 'context_update') {
      if (_contextWindowPayloadFromOp(op) !== undefined) add(_contextDeltaAgentId(op));
      continue;
    }
    if (op.op !== 'agent_upsert') continue;
    const hint = hints && hints[i] ? hints[i] : {};
    const previous = hint && hint.agent ? hint.agent : null;
    const next = _agentNextFromDelta(op, previous);
    if (_contextWindowPayloadFromOp(op) !== undefined
        && _agentDeltaIsContextWindowOnly(previous, next, op)) add(op.id);
  }
  return ids;
}

function _peerMessageDeltaAgentIds(op) {
  const ids = [];
  const add = function(value) {
    value = String(value || '').trim();
    if (value && ids.indexOf(value) < 0) ids.push(value);
  };
  add(op && op.agent_id);
  add(op && op.sender_id);
  add(op && op.recipient_id);
  const message = (op && op.message) || {};
  add(message.agent_id);
  add(message.sender_id);
  add(message.recipient_id);
  return ids;
}

function _directMessageDeltaMessageId(op, message) {
  return String(
    (message && (message.message_id || message.id))
      || (op && (op.message_id || op.id))
      || ''
  ).trim();
}

function _deltaTimestampValue(value) {
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

function _sortAgentPeerThreadMap(map) {
  const items = [];
  const source = (map && typeof map === 'object') ? map : {};
  for (const key in source) {
    const thread = source[key];
    if (!thread) continue;
    const id = String((thread && thread.thread_id) || key || '').trim();
    if (!id) continue;
    items.push({ id: id, thread: Object.assign({}, thread) });
  }
  items.sort(function(a, b) {
    const at = _deltaTimestampValue(a.thread && a.thread.last_activity_at);
    const bt = _deltaTimestampValue(b.thread && b.thread.last_activity_at);
    if (at !== bt) return bt - at;
    const am = String((a.thread && a.thread.last_message_id) || '');
    const bm = String((b.thread && b.thread.last_message_id) || '');
    if (am !== bm) return bm.localeCompare(am);
    return String(b.id || '').localeCompare(String(a.id || ''));
  });
  const out = {};
  for (let i = 0; i < items.length; i++) out[items[i].id] = items[i].thread;
  return out;
}

function _terminalWorkspaceViewedCellBeforeDelta() {
  if (!state || !state.agents) return null;
  let cell = null;
  if (selectedTerminalId && state.agents[selectedTerminalId]) {
    cell = state.agents[selectedTerminalId];
  }
  if (!cell && state.active_session_id) {
    for (const id in state.agents) {
      const candidate = state.agents[id];
      if (candidate && candidate.session_id === state.active_session_id) {
        cell = candidate;
        break;
      }
    }
  }
  if (!cell && selectedAgentId && state.agents[selectedAgentId]) {
    cell = state.agents[selectedAgentId];
  }
  return cell || null;
}

function _terminalWorkspaceViewedAgentIdBeforeDelta() {
  const cell = _terminalWorkspaceViewedCellBeforeDelta();
  if (!cell) return '';
  if (cell.cell_type === 'terminal') {
    const parentId = String(cell.parent_id || '').trim();
    return parentId && state && state.agents && state.agents[parentId] ? parentId : '';
  }
  return cell.cell_type === 'agent' ? String(cell.id || '') : '';
}

function _terminalWorkspaceViewedCellIdBeforeDelta() {
  const cell = _terminalWorkspaceViewedCellBeforeDelta();
  return String((cell && cell.id) || '').trim();
}

function _terminalWorkspaceGroupBeforeDelta() {
  const cell = _terminalWorkspaceViewedCellBeforeDelta();
  if (cell && cell.group) return String(cell.group || '');
  if (typeof _activeGroup === 'function') return String(_activeGroup() || '');
  return String((state && state.active_group) || '');
}

function _groupDeltaInvalidatesTerminalWorkspace(op, hint) {
  const viewedGroup = _terminalWorkspaceGroupBeforeDelta();
  if (!viewedGroup) return true;
  switch (op && op.op) {
    case 'group_update':
    case 'group_remove':
    case 'group_settings_update': {
      const group = String((op && (op.name || op.group || op.group_name)) || (hint && hint.group) || '');
      return group ? group === viewedGroup : true;
    }
    case 'group_rename': {
      const oldName = String((op && op.old_name) || '');
      const newName = String((op && op.new_name) || '');
      return oldName ? (oldName === viewedGroup || newName === viewedGroup) : true;
    }
    case 'groups_reorder':
      return true;
    default:
      return false;
  }
}

function _globalSettingsDeltaInvalidatesTerminalWorkspace(op) {
  if (!_globalSettingsDeltaHasChangedKeys(op)) return true;
  return _globalSettingsDeltaChangedKeys(op).indexOf('xterm_scrollback') >= 0;
}

function _agentDeltaInvalidatesTerminalWorkspace(previous, next, op) {
  const agentId = String((op && op.id) || (previous && previous.id) || (next && next.id) || '');
  if (!agentId) return true;
  const viewedCell = _terminalWorkspaceViewedCellBeforeDelta();
  const viewedCellId = String((viewedCell && viewedCell.id) || '');
  const viewedAgentId = _terminalWorkspaceViewedAgentIdBeforeDelta();
  const selectedIds = [
    viewedCellId,
    viewedAgentId,
    String(typeof selectedTerminalId !== 'undefined' ? (selectedTerminalId || '') : ''),
    String(typeof selectedAgentId !== 'undefined' ? (selectedAgentId || '') : ''),
    String(typeof focusedItemId !== 'undefined' ? (focusedItemId || '') : ''),
  ].filter(Boolean);
  if (!viewedCell && !selectedIds.length) return true;
  if (selectedIds.indexOf(agentId) >= 0) return true;

  const prevParent = String((previous && previous.parent_id) || '');
  const nextParent = String((next && next.parent_id) || '');
  const viewedParent = String((viewedCell && viewedCell.parent_id) || '');
  if (viewedParent && agentId === viewedParent) return true;
  if (viewedAgentId && (prevParent === viewedAgentId || nextParent === viewedAgentId)) return true;
  if (viewedCellId && (prevParent === viewedCellId || nextParent === viewedCellId)) return true;

  const activeSession = String((state && state.active_session_id) || '');
  const prevSession = String((previous && previous.session_id) || '');
  const nextSession = String((next && next.session_id) || '');
  if (activeSession && (prevSession === activeSession || nextSession === activeSession)) return true;
  return false;
}

function _globalSettingsDeltaChangedKeys(op) {
  return Array.isArray(op && op.changed_keys)
    ? op.changed_keys.map(function(key) { return String(key || ''); }).filter(Boolean)
    : [];
}

function _globalSettingsDeltaHasChangedKeys(op) {
  return Array.isArray(op && op.changed_keys);
}

function _globalSettingsDeltaSkipsBroadInvalidation(op) {
  if (!_globalSettingsDeltaHasChangedKeys(op)) return false;
  var changed = _globalSettingsDeltaChangedKeys(op);
  return changed.every(function(key) {
    return key === 'status_bar_visibility' || key.indexOf('ai_') === 0;
  });
}

function _deltaSurfaceInvalidations(ops, hints) {
  const flags = _blankSurfaceInvalidations();
  // TORQUE:236 v13 instrumentation: when window.__torqueDebugRender is true,
  // record which delta op type flipped flags.engineer to true so the
  // user's reproduction shows what's slipping through the gates.
  const _debug = (typeof window !== 'undefined' && window.__torqueDebugRender);
  let _engBefore = false;
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i];
    const hint = hints && hints[i] ? hints[i] : {};
    if (_debug) _engBefore = !!flags.engineer;
    const registered = _deltaOperationSpec(op.op);
    if (registered && typeof registered.invalidate === 'function') {
      registered.invalidate(flags, op, hint);
      continue;
    }
    switch (op.op) {
      case 'agent_upsert':
      case 'agent_remove':
        _applyAgentSurfaceInvalidation(flags, op, hint);
        break;
      case 'group_update':
      case 'group_remove':
      case 'group_rename':
      case 'groups_reorder':
      case 'group_settings_update':
        _markSurface(flags, 'main', 'context', 'engineer');
        if (_groupDeltaInvalidatesTerminalWorkspace(op, hint)) _markSurface(flags, 'terminal');
        break;
      case 'global_settings_update':
        if (!_globalSettingsDeltaSkipsBroadInvalidation(op)) {
          _markSurface(flags, 'main', 'context', 'engineer');
          if (_globalSettingsDeltaInvalidatesTerminalWorkspace(op)) _markSurface(flags, 'terminal');
        }
        break;
      case 'ai_settings_update':
      case 'ai_index_status_update':
      case 'ai_summary_status_update':
        // Consumed by ai_settings.js with targeted DOM updates when the Settings
        // → AI tab is open. Do not mark broad settings/panel surfaces; those
        // rerenders would clobber focus/caret in the modal.
        break;
      case 'focus_update':
        // focus_update carries PTY session/window focus
        // (`active_session_id` / `current_window_id`), not agent-panel
        // selection state. Mark only surfaces that display active-terminal
        // state (main grid indicator and terminal-related context views).
        _markSurface(flags, 'main', 'context', 'terminal');
        break;
      case 'task_upsert':
      case 'task_remove':
        _applyTaskSurfaceInvalidation(flags, op, hint);
        if (_taskDeltaInvalidatesInitiatives(hint && hint.task, _taskNextFromDelta(op, hint && hint.task), op)) {
          _markSurface(flags, 'initiatives');
        }
        break;
      case 'lanes_update':
      case 'schedule_upsert':
      case 'schedule_remove':
        _markSurface(flags, 'board');
        break;
      case 'event_append':
      case 'mcp_call_append': {
        // The Events panel renders panel_events plus inline task threads;
        // MCP calls never appear there. Marking 'events' for
        // mcp_call_append — the highest-frequency op in the system —
        // forced a full renderEvents() per worker tool call with no
        // visible change.
        if (op.op === 'event_append') _markSurface(flags, 'events');
        // Engineer panel only needs a full re-render when the append
        // belongs to the focused agent *and* the focused panel is displaying
        // the affected sub-surface. Cross-agent traffic (e.g. another worker
        // firing torque_progress while the user is reading a different agent's
        // panel) used to clobber the focused panel's DOM — destroying any
        // in-progress textarea selection / scroll anchor — even though
        // nothing the panel displayed had changed.
        const _appendFocusedId = _contextFocusedAgentBeforeDelta();
        const _appendCellId = (op.op === 'mcp_call_append')
          ? String((op.call && op.call.cell_id) || '')
          : String(op.cell_id || '');
        if (_focusAppendInvalidatesFocusPanel(_appendFocusedId, _appendCellId)) {
          _markSurface(flags, 'focus');
        }
        if (_appendFocusedId && _appendCellId
            && _appendFocusedId === _appendCellId
            && (op.op !== 'mcp_call_append'
              || _focusedAgentMcpEventsSubtabActive(_appendFocusedId))) {
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'journal_append':
      case 'journal_delete': {
        const _journalFocused = _focusedEngineerAgent();
        const _journalAuthorId = String((op && op.author_cell_id) || '');
        if (_journalFocused && _journalAuthorId
            && String(_journalFocused.id || '') === _journalAuthorId) {
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'digest_buffer_stats':
      case 'digest_sent_push':
      case 'engineer_buffer_stats':
      case 'engineer_sent_events':
      case 'engineer_worklog_append':
      case 'engineer_streams':
      case 'engineer_streams_update': {
        // These ops are scoped to a specific engineer's group. The
        // engineer panel only displays the focused engineer's stream /
        // worklog / digest data, so a delta for engineer A's group
        // shouldn't clobber engineer B's panel — high-frequency stream
        // updates were the residual firehose surviving v4 + v5.
        const _engOpGroup = String((op && op.group) || '');
        if (!_engOpGroup) {
          _markSurface(flags, 'engineer');
          break;
        }
        const _engFocused = _focusedEngineerAgent();
        if (_engFocused && String(_engFocused.group || '') === _engOpGroup) {
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'architect_journal_append': {
        // Decisions still affect the main grid (decision count badges
        // etc.). Engineer-panel refresh is only needed when the focused
        // agent is the architect this entry belongs to — otherwise the
        // panel doesn't display this data. (Frequent: architects journal
        // dozens of entries per debug session.)
        if (String((op && op.type) || '').toLowerCase() === 'decision') {
          _markSurface(flags, 'main');
        }
        const _ajFocused = _focusedEngineerAgent();
        const _ajArchId = String((op && op.architect_id) || '');
        if (_ajFocused && _ajArchId
            && String(_ajFocused.id || '') === _ajArchId) {
          _markSurface(flags, 'focus', 'engineer');
        }
        break;
      }
      case 'architect_dismissed':
      case 'architect_rehired': {
        const _archLifeFocused = _focusedEngineerAgent();
        const _archLifeId = String((op && op.architect_id) || '');
        if (_archLifeFocused && _archLifeId
            && String(_archLifeFocused.id || '') === _archLifeId) {
          _markSurface(flags, 'engineer');
        }
        if (_architectPeerRosterDeltaInvalidatesFocusedMessages(null, null, op)) {
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'peer_message_upsert':
      case 'direct_message_upsert':
      case 'direct_message_read': {
        const _pmFocused = _focusedEngineerAgent();
        const _pmIds = _peerMessageDeltaAgentIds(op);
        if (op.op !== 'peer_message_upsert') {
          const _terminalViewedAgentId = _terminalWorkspaceViewedAgentIdBeforeDelta();
          if (_terminalViewedAgentId && _pmIds.indexOf(_terminalViewedAgentId) >= 0) {
            _markSurface(flags, 'main', 'terminal');
          }
        }
        if (_pmFocused && _pmIds.indexOf(String(_pmFocused.id || '')) >= 0) {
          _markSurface(flags, 'focus', 'engineer');
        }
        break;
      }
      case 'agent_message_loop_upsert': {
        const _loopAgentId = String((op.loop && op.loop.agent_id) || op.agent_id || '');
        const _terminalViewedAgentId = _terminalWorkspaceViewedAgentIdBeforeDelta();
        if (_terminalViewedAgentId && _loopAgentId === _terminalViewedAgentId) {
          _markSurface(flags, 'main', 'terminal');
        }
        break;
      }
      case 'agent_peer_thread_upsert':
      case 'agent_peer_thread_remove':
        _markSurface(flags, 'chat');
        break;
      case 'engineer_settings_update': {
        _markSurface(flags, 'main');
        const _esFocused = _focusedEngineerAgent();
        const _esGroup = String((op && op.group) || '');
        if (_esFocused && _esGroup
            && String(_esFocused.group || '') === _esGroup) {
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'agent_digest_update': {
        _markSurface(flags, 'main');
        const _adFocused = _contextFocusedAgentBeforeDelta();
        const _adCellId = String((op && op.cell_id) || '');
        if (_adFocused && _adCellId && _adFocused === _adCellId) {
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'decision_upsert':
      case 'decision_remove':
      case 'pending_hire_upsert':
      case 'pending_hire_resolve': {
        _markSurface(flags, 'main');
        const _dpFocused = _focusedEngineerAgent();
        let _dpArchId = String((op && op.architect_id) || '');
        // _remove / _resolve ops carry only the record id; resolve the
        // architect via the cached record so the focused-architect gate
        // still works.
        if (!_dpArchId && op && op.id) {
          const _opOpName = String(op.op || '');
          let _existing = null;
          if (_opOpName === 'decision_remove'
              && state && state.decisions) {
            _existing = state.decisions[op.id] || null;
          } else if (_opOpName === 'pending_hire_resolve'
              && state && state.pending_hires) {
            _existing = state.pending_hires[op.id] || null;
          }
          if (_existing && _existing.architect_id) {
            _dpArchId = String(_existing.architect_id || '');
          }
        }
        if (_dpFocused && _dpArchId
            && String(_dpFocused.id || '') === _dpArchId) {
          _markSurface(flags, 'focus', 'engineer');
        } else if (!_dpArchId) {
          // No way to tell which architect this belongs to — be safe and
          // refresh. (Preserves legacy behavior for ops missing the field.)
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'initiative_upsert':
      case 'initiative_link_upsert':
      case 'initiative_link_remove':
      case 'area_upsert':
      case 'area_link_upsert':
      case 'area_link_remove':
      case 'area_note_upsert':
      case 'planning_area_upsert':
      case 'planning_area_link_upsert':
      case 'planning_area_link_remove':
      case 'planning_area_note_upsert':
        _markSurface(flags, 'initiatives');
        break;
      case 'thinking_scratchpad_note_upsert':
        _markSurface(flags, 'thinking');
        break;
      case 'idea_brief_upsert':
        _markSurface(flags, 'thinking');
        break;
      case 'behavior_overlay_version_append':
      case 'behavior_overlay_active_update':
      case 'behavior_overlay_proposal_upsert':
      case 'behavior_overlay_proposal_resolve': {
        if (typeof behaviorOverlayDeltaInvalidatesFocusedPanel === 'function'
            && behaviorOverlayDeltaInvalidatesFocusedPanel(op)) {
          _markSurface(flags, 'engineer');
        }
        break;
      }
      case 'ui_update':
        _applyUiSurfaceInvalidation(flags, op.key);
        break;
    }
    if (_debug && !_engBefore && flags.engineer) {
      try {
        const _opSummary = {
          op: op.op,
          id: op.id || '',
          group: op.group || '',
          cell_id: op.cell_id || (op.call && op.call.cell_id) || '',
          author_cell_id: op.author_cell_id || '',
          architect_id: op.architect_id || '',
        };
        console.warn('[torque render] engineer-flag set by op:',
          JSON.stringify(_opSummary));
      } catch (_e) {}
    }
  }
  return flags;
}

function _taskNextFromDelta(op, previous) {
  if (!op || op.op === 'task_remove') return null;
  const next = Object.assign({}, previous || {}, op || {});
  delete next.op;
  return next;
}

function _agentNextFromDelta(op, previous) {
  if (!op || op.op === 'agent_remove') return null;
  const next = Object.assign({}, previous || {}, op || {});
  delete next.op;
  return next;
}

function _agentDeltaChangedFields(previous, next, op) {
  const fields = {};
  const keys = {};
  for (const key in (previous || {})) keys[key] = true;
  for (const key in (next || {})) keys[key] = true;
  for (const key in (op || {})) keys[key] = true;
  delete keys.op;
  for (const key in keys) {
    const a = previous ? previous[key] : undefined;
    const b = next ? next[key] : undefined;
    if (!_deltaValuesEqual(a, b)) fields[key] = true;
  }
  return fields;
}

function _agentDeltaIsContextWindowOnly(previous, next, op) {
  if (!op || op.op !== 'agent_upsert') return false;
  if (!previous || !next) return false;
  if (!Object.prototype.hasOwnProperty.call(op, 'context_window')
      && !Object.prototype.hasOwnProperty.call(op, 'provider_usage')) return false;
  const changed = _agentDeltaChangedFields(previous, next, op);
  const allowed = {
    context_window: true,
    provider_usage: true,
    last_heartbeat_at: true,
    last_activity_at: true,
    last_event_at: true,
    last_event_text: true,
  };
  for (const key in changed) {
    if (!allowed[key]) return false;
  }
  return true;
}

function _stableDeltaValue(value) {
  if (value === undefined) return '__torque_undefined__';
  if (value === null) return null;
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch (_err) {
      return String(value);
    }
  }
  return value;
}

function _deltaValuesEqual(a, b) {
  return _stableDeltaValue(a) === _stableDeltaValue(b);
}

function _deltaObjectFieldsChanged(previous, next, candidateFields) {
  const changed = {};
  const fields = candidateFields || [];
  for (let i = 0; i < fields.length; i++) {
    const field = fields[i];
    const a = previous ? previous[field] : undefined;
    const b = next ? next[field] : undefined;
    if (!_deltaValuesEqual(a, b)) changed[field] = true;
  }
  return changed;
}

function _deltaHasChangedField(changed, fields) {
  for (let i = 0; i < fields.length; i++) {
    if (changed[fields[i]]) return true;
  }
  return false;
}

function _deltaIsDispatchStateOnly(changed) {
  let sawDispatchState = false;
  for (const key in (changed || {})) {
    if (key === 'dispatch_state') {
      sawDispatchState = true;
    } else if (key !== 'updated_at') {
      return false;
    }
  }
  return sawDispatchState;
}

function _taskTouchesGroup(previous, next, group) {
  if (!group) return true;
  const prevGroup = previous ? (previous.group || '') : '';
  const nextGroup = next ? (next.group || '') : '';
  return prevGroup === group || nextGroup === group;
}

function _agentTouchesGroup(previous, next, group) {
  if (!group) return true;
  const prevGroup = previous ? (previous.group || '') : '';
  const nextGroup = next ? (next.group || '') : '';
  return prevGroup === group || nextGroup === group;
}

function _boardCurrentGroupFilterEnabled() {
  return typeof _boardFilterByGroup === 'undefined' || !!_boardFilterByGroup;
}

function _eventsCurrentGroupFilterEnabled() {
  return typeof _eventsFilterByGroup === 'undefined' || !!_eventsFilterByGroup;
}

function _currentSurfaceGroup() {
  return (typeof _currentGroup === 'function') ? (_currentGroup() || '') : '';
}

function _taskHasHumanAskLabel(task) {
  const labels = task && Array.isArray(task.labels) ? task.labels : [];
  return !!(
    task
    && labels.indexOf('torque:human') >= 0
    && labels.indexOf('torque:non-user-ask') < 0
  );
}

function _taskIsOpenAsk(task) {
  return !!(task && _taskHasHumanAskLabel(task) && (task.lane || '') !== 'Done');
}

function _taskIsAskParent(taskId, group) {
  if (!taskId || !state || !state.board_tasks) return false;
  const tasks = state.board_tasks || {};
  for (const id in tasks) {
    const task = tasks[id];
    if (!task || task.parent_task_id !== taskId) continue;
    if (!_taskIsOpenAsk(task)) continue;
    if (group && task.group !== group) continue;
    return true;
  }
  return false;
}

function _taskDeltaChangedFields(previous, next, op) {
  const fields = {};
  const keys = {};
  for (const key in (previous || {})) keys[key] = true;
  for (const key in (next || {})) keys[key] = true;
  for (const key in (op || {})) keys[key] = true;
  delete keys.op;
  for (const key in keys) {
    const a = previous ? previous[key] : undefined;
    const b = next ? next[key] : undefined;
    if (!_deltaValuesEqual(a, b)) fields[key] = true;
  }
  return fields;
}

function _taskHasBranchBoundaryMainRelevance(task) {
  if (!task) return false;
  const boundary = task.worktree_boundary && typeof task.worktree_boundary === 'object'
    ? task.worktree_boundary
    : null;
  if (boundary && boundary.repo_root && boundary.branch) return true;
  return !!task.resume_after_boundary_task_id;
}

function _taskDeltaInvalidatesBoundaryMain(previous, next, changed) {
  if (!_taskHasBranchBoundaryMainRelevance(previous)
      && !_taskHasBranchBoundaryMainRelevance(next)) {
    return false;
  }
  if (!previous || !next) return true;
  return _deltaHasChangedField(changed, [
    'task',
    'lane',
    'status',
    'finalization_status',
    'created_at',
    'updated_at',
    'lane_entered_at',
    'worktree_boundary',
    'resume_after_boundary_task_id',
  ]);
}

function _taskDeltaInvalidatesMain(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  if (!previous && !next) return false;
  const changed = _taskDeltaChangedFields(previous, next, op);
  const prevAgent = previous ? String(previous.agent_id || '') : '';
  const nextAgent = next ? String(next.agent_id || '') : '';
  if (prevAgent || nextAgent) {
    if (prevAgent !== nextAgent) return true;
    if (_deltaIsDispatchStateOnly(changed)) return false;
    return _deltaHasChangedField(changed, [
      'task',
      'lane',
      'status',
      'finalization_status',
      'action_name',
      'description',
      'messages',
      'messages_thread',
      'created_at',
      'updated_at',
      'started_at',
      'worktree_boundary',
      'resume_after_boundary_task_id',
      'artifacts',
      'attachments',
    ]);
  }
  if (_taskDeltaInvalidatesBoundaryMain(previous, next, changed)) return true;
  return _deltaHasChangedField(changed, [
    'worktree_boundary',
    'resume_after_boundary_task_id',
  ]);
}

function _taskDeltaInvalidatesInitiatives(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const group = _currentSurfaceGroup();
  if (!_taskTouchesGroup(previous, next, group)) return false;
  if (!previous || !next) return true;
  const changed = _taskDeltaChangedFields(previous, next, op);
  if (_deltaIsDispatchStateOnly(changed)) return false;
  return _deltaHasChangedField(changed, [
    'id',
    'group',
    'task',
    'lane',
    'status',
    'finalization_status',
    'health_state',
    'archived_at',
  ]);
}

function _taskDeltaInvalidatesBoard(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const group = _boardCurrentGroupFilterEnabled() ? _currentSurfaceGroup() : '';
  if (!_taskTouchesGroup(previous, next, group)) return false;
  if (!previous || !next) return true;
  const changed = _taskDeltaChangedFields(previous, next, op);
  if (_deltaIsDispatchStateOnly(changed)) return false;
  const alwaysFields = [
    'id',
    'group',
    'lane',
    'position',
    'parent_task_id',
    'pipeline_depth',
    'depends_on',
    'task',
    'labels',
    'action_name',
    'agent_template',
    'agent_id',
    'status',
    'finalization_status',
    'health_state',
    'health_details',
    'health_since',
    'verification_mode',
    'verification_state',
    'verification_notes',
    'verification_summary',
    'completion_evidence',
    'attachments',
    'artifacts',
    'board_sync',
    'provider',
    'external_id',
    'external_url',
    'messages',
    'messages_thread',
    'created_by',
    'assigned_architect_id',
    'created_by_architect_id',
    'created_by_engineer_id',
    'created_at',
    'updated_at',
    'lane_entered_at',
    'scheduled_at',
    'worktree_boundary',
    'resume_after_boundary_task_id',
  ];
  if (_deltaHasChangedField(changed, alwaysFields)) return true;
  const searchActive = !!(typeof _boardSearchQuery !== 'undefined' && _boardSearchQuery);
  if (searchActive && _deltaHasChangedField(changed, [
    'description',
    'assigned_engineer_id',
    'assigned_architect_id',
  ])) return true;
  return false;
}

function _contextFocusedTaskBeforeDelta() {
  if (typeof _contextCurrentTask === 'function') {
    const task = _contextCurrentTask();
    return task ? String(task.id || '') : '';
  }
  if (typeof _boardFocusedTask !== 'undefined' && _boardFocusedTask) {
    return String(_boardFocusedTask || '');
  }
  return '';
}

function _contextFocusedAgentBeforeDelta() {
  if (typeof _contextCurrentAgent === 'function') {
    const agent = _contextCurrentAgent();
    return agent ? String(agent.id || '') : '';
  }
  if (typeof selectedAgentId !== 'undefined' && selectedAgentId) {
    return String(selectedAgentId || '');
  }
  if (typeof focusedItemId !== 'undefined' && focusedItemId
      && state && state.agents && state.agents[focusedItemId]) {
    return String(focusedItemId || '');
  }
  return '';
}

function _focusedAgentMcpEventsSubtabActive(agentId) {
  agentId = String(agentId || '');
  if (!agentId || !state || !state.agents) return false;
  const agent = state.agents[agentId];
  if (!agent) return false;
  if (typeof _agentPanelIsMcpSubtabActive === 'function') {
    return !!_agentPanelIsMcpSubtabActive(agent);
  }
  if (typeof _agentPanelKind !== 'function'
      || typeof _agentPanelActiveTab !== 'function'
      || typeof _agentPanelEventsInnerTab !== 'function') {
    return false;
  }
  return _agentPanelActiveTab(_agentPanelKind(agent)) === 'events'
    && _agentPanelEventsInnerTab(agent) === 'mcp';
}

function _taskPipelineRef(task) {
  return task ? String(task.pipeline_root_id || task.id || '') : '';
}

function _taskDeltaMayChangeContextCurrentTask(previous, next, focusedAgentId) {
  if (!focusedAgentId) return false;
  const prevAgent = previous ? String(previous.agent_id || '') : '';
  const nextAgent = next ? String(next.agent_id || '') : '';
  if (prevAgent === focusedAgentId || nextAgent === focusedAgentId) return true;
  return false;
}

function _taskDeltaInvalidatesContext(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const focus = (typeof _contextFocus !== 'undefined') ? String(_contextFocus || 'group') : 'group';
  const focusedTaskId = _contextFocusedTaskBeforeDelta();
  const focusedAgentId = _contextFocusedAgentBeforeDelta();
  const taskId = String((op && op.id) || (next && next.id) || (previous && previous.id) || '');
  const changed = _taskDeltaChangedFields(previous, next, op);
  if (focus === 'task') {
    if (taskId && taskId === focusedTaskId) return true;
    return _taskDeltaMayChangeContextCurrentTask(previous, next, focusedAgentId);
  }
  if (focus === 'pipeline') {
    const current = focusedTaskId && state && state.board_tasks
      ? _taskPipelineRef(state.board_tasks[focusedTaskId])
      : '';
    if (!current && _taskDeltaMayChangeContextCurrentTask(previous, next, focusedAgentId)) return true;
    if (current && (_taskPipelineRef(previous) === current || _taskPipelineRef(next) === current)) return true;
    return _deltaHasChangedField(changed, ['pipeline_root_id'])
      && _taskDeltaMayChangeContextCurrentTask(previous, next, focusedAgentId);
  }
  if (focus === 'agent') {
    return _taskDeltaMayChangeContextCurrentTask(previous, next, focusedAgentId);
  }
  if (taskId && taskId === focusedTaskId) {
    return _deltaHasChangedField(changed, ['task', 'agent_id', 'lane', 'pipeline_root_id']);
  }
  return false;
}

function _taskDeltaInvalidatesEvents(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const group = _eventsCurrentGroupFilterEnabled() ? _currentSurfaceGroup() : '';
  if (!_taskTouchesGroup(previous, next, group)) return false;
  const wasAsk = _taskIsOpenAsk(previous);
  const isAsk = _taskIsOpenAsk(next);
  if (wasAsk || isAsk) {
    const changed = _taskDeltaChangedFields(previous, next, op);
    return !previous || !next || _deltaHasChangedField(changed, [
      'group',
      'labels',
      'lane',
      'task',
      'description',
      'created_at',
      'parent_task_id',
    ]);
  }
  const taskId = String((op && op.id) || (previous && previous.id) || (next && next.id) || '');
  if (_taskIsAskParent(taskId, group)) {
    const changed = _taskDeltaChangedFields(previous, next, op);
    return !previous || !next || _deltaHasChangedField(changed, [
      'task',
      'description',
      'agent_id',
      'group',
    ]);
  }
  {
    const changed = _taskDeltaChangedFields(previous, next, op);
    if (_deltaHasChangedField(changed, ['messages_thread'])) return true;
  }
  return false;
}

function _focusedEngineerAgent() {
  if (typeof _resolveFocusedAgent === 'function') return _resolveFocusedAgent();
  if (typeof focusedItemId !== 'undefined'
      && focusedItemId
      && state
      && state.agents
      && state.agents[focusedItemId]) {
    return state.agents[focusedItemId];
  }
  return null;
}

function _focusedEngineerAgentKind(agent) {
  if (typeof _agentPanelKind === 'function') return _agentPanelKind(agent);
  return String((agent && agent.kind) || 'worker');
}

function _focusedEngineerActiveTab(kind) {
  if (typeof _agentPanelActiveTab === 'function') return _agentPanelActiveTab(kind);
  return '';
}

function _taskMessagesThreadTouchesAgent(task, agentId) {
  if (!task || !agentId) return false;
  const thread = Array.isArray(task.messages_thread) ? task.messages_thread : [];
  for (let i = 0; i < thread.length; i++) {
    const entry = thread[i] || {};
    const recipientId = String(entry.recipient_agent_id || '');
    if (recipientId && recipientId === agentId) return true;
  }
  return String(task.agent_id || '') === agentId && thread.length > 0;
}

function _focusedAgentIdForFocusPanel() {
  if (typeof selectedAgentId === 'undefined' || !selectedAgentId) return '';
  return String(selectedAgentId || '');
}

function _focusPanelAgentTouchesFocused(previous, next, op) {
  const focusedId = _focusedAgentIdForFocusPanel();
  if (!focusedId) return false;
  const agentId = String((op && op.id) || (previous && previous.id) || (next && next.id) || '');
  if (agentId && agentId === focusedId) return true;
  const prevParent = previous ? String(previous.parent_id || '') : '';
  const nextParent = next ? String(next.parent_id || '') : '';
  return prevParent === focusedId || nextParent === focusedId;
}

function _agentDeltaInvalidatesFocusPanel(previous, next, op) {
  if (!_focusPanelAgentTouchesFocused(previous, next, op)) return false;
  const focusedId = _focusedAgentIdForFocusPanel();
  const focused = focusedId && state && state.agents ? state.agents[focusedId] : null;
  const focusedGroup = String((focused && focused.group) || (previous && previous.group) || (next && next.group) || '');
  if (!focusedGroup) return true;
  return _agentTouchesGroup(previous, next, focusedGroup);
}

function _taskDeltaInvalidatesFocusPanel(previous, next, op) {
  const focusedId = _focusedAgentIdForFocusPanel();
  if (!focusedId) return false;
  const prevAgent = previous ? String(previous.agent_id || '') : '';
  const nextAgent = next ? String(next.agent_id || '') : '';
  const prevEngineer = previous ? String(previous.assigned_engineer_id || '') : '';
  const nextEngineer = next ? String(next.assigned_engineer_id || '') : '';
  return prevAgent === focusedId || nextAgent === focusedId
    || prevEngineer === focusedId || nextEngineer === focusedId;
}

function _focusAppendInvalidatesFocusPanel(focusedId, cellId) {
  focusedId = String(focusedId || '');
  cellId = String(cellId || '');
  if (!focusedId || !cellId) return false;
  if (focusedId === cellId) return true;
  const cell = state && state.agents ? state.agents[cellId] : null;
  return !!(cell && String(cell.parent_id || '') === focusedId);
}

function _taskDeltaInvalidatesEngineer(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const group = _currentSurfaceGroup();
  if (!_taskTouchesGroup(previous, next, group)) return false;
  const changed = _taskDeltaChangedFields(previous, next, op);
  if (_deltaIsDispatchStateOnly(changed)) return false;
  const focused = _focusedEngineerAgent();
  if (!focused) return false;
  const kind = _focusedEngineerAgentKind(focused);
  const tab = _focusedEngineerActiveTab(kind);
  if (kind === 'worker') {
    if (tab && tab !== 'worklog' && tab !== 'messages') return false;
    const focusedId = String(focused.id || '');
    if (tab === 'messages') {
      const changed = _taskDeltaChangedFields(previous, next, op);
      if (!_deltaHasChangedField(changed, ['messages_thread'])) return false;
      return !!(
        focusedId
        && (_taskMessagesThreadTouchesAgent(previous, focusedId)
          || _taskMessagesThreadTouchesAgent(next, focusedId))
      );
    }
    return !!(
      focusedId
      && ((previous && String(previous.agent_id || '') === focusedId)
        || (next && String(next.agent_id || '') === focusedId))
    );
  }
  if (kind === 'engineer') {
    if (tab && tab !== 'worklog' && tab !== 'queued') return false;
    const focusedId = String(focused.id || '');
    return !!(
      focusedId
      && ((previous && String(previous.assigned_engineer_id || '') === focusedId)
        || (next && String(next.assigned_engineer_id || '') === focusedId))
    );
  }
  return false;
}

function _applyTaskSurfaceInvalidation(flags, op, hint) {
  const previous = hint && hint.task ? hint.task : null;
  const next = _taskNextFromDelta(op, previous);
  if (!_standaloneDeltaOptimizationsEnabled()) {
    _markSurface(flags, 'main', 'board', 'context', 'events', 'engineer');
    if (_taskDeltaInvalidatesFocusPanel(previous, next, op)) _markSurface(flags, 'focus');
    return;
  }
  if (_taskDeltaInvalidatesMain(previous, next, op)) _markSurface(flags, 'main');
  if (_taskDeltaInvalidatesBoard(previous, next, op)) _markSurface(flags, 'board');
  if (_taskDeltaInvalidatesContext(previous, next, op)) _markSurface(flags, 'context');
  if (_taskDeltaInvalidatesEvents(previous, next, op)) _markSurface(flags, 'events');
  if (_taskDeltaInvalidatesFocusPanel(previous, next, op)) _markSurface(flags, 'focus');
  if (_taskDeltaInvalidatesEngineer(previous, next, op)) _markSurface(flags, 'engineer');
}

function _agentHasAttention(agent) {
  return !!(agent && agent.needs_attention && agent.cell_type !== 'terminal');
}

function _agentIsAskParentAgent(agentId, group) {
  if (!agentId || !state || !state.board_tasks) return false;
  const tasks = state.board_tasks || {};
  for (const id in tasks) {
    const ask = tasks[id];
    if (!_taskIsOpenAsk(ask)) continue;
    if (group && ask.group !== group) continue;
    const parentId = ask.parent_task_id || '';
    const parent = parentId ? tasks[parentId] : null;
    if (parent && String(parent.agent_id || '') === String(agentId || '')) return true;
  }
  return false;
}

function _agentDeltaInvalidatesContext(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const focusedAgentId = _contextFocusedAgentBeforeDelta();
  const agentId = String((op && op.id) || (previous && previous.id) || (next && next.id) || '');
  if (!agentId || agentId !== focusedAgentId) return false;
  return true;
}

function _agentDeltaInvalidatesEvents(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const group = _eventsCurrentGroupFilterEnabled() ? _currentSurfaceGroup() : '';
  if (!_agentTouchesGroup(previous, next, group)) return false;
  const wasAttention = _agentHasAttention(previous);
  const isAttention = _agentHasAttention(next);
  if (wasAttention || isAttention) {
    const changed = _deltaObjectFieldsChanged(previous, next, [
      'group',
      'name',
      'needs_attention',
      'error_message',
      'activity_detail',
      'last_event_at',
      'cell_type',
    ]);
    return !previous || !next || _deltaHasChangedField(changed, [
      'group',
      'name',
      'needs_attention',
      'error_message',
      'activity_detail',
      'last_event_at',
      'cell_type',
    ]);
  }
  const agentId = String((op && op.id) || (previous && previous.id) || (next && next.id) || '');
  if (_agentIsAskParentAgent(agentId, group)) {
    const changed = _deltaObjectFieldsChanged(previous, next, [
      'group',
      'name',
      'slug',
    ]);
    return !previous || !next || _deltaHasChangedField(changed, ['group', 'name', 'slug']);
  }
  return false;
}

function _agentDeltaInvalidatesEngineer(previous, next, op) {
  if (!_standaloneDeltaOptimizationsEnabled()) return true;
  const group = _currentSurfaceGroup();
  if (!_agentTouchesGroup(previous, next, group)) return false;
  const focused = _focusedEngineerAgent();
  if (!focused) return false;
  const agentId = String((op && op.id) || (previous && previous.id) || (next && next.id) || '');
  const focusedId = String(focused.id || '');
  if (agentId && focusedId && agentId === focusedId) return true;
  // Engineer-kind focus: workers list / worklog views read related workers.
  // For now, only refresh on agents the focused engineer owns. Cross-engineer
  // / cross-owner traffic in the same group used to refresh unconditionally
  // (the trailing `return true` here), which clobbered the focused panel's
  // textarea + scroll on every worker activity pulse — the dominant firehose
  // surviving the TORQUE:236 v4 mcp/event surface gate.
  const focusedKind = _focusedEngineerAgentKind(focused);
  if (focusedKind === 'architect') {
    const hiredPrev = previous ? String(previous.hired_by_architect_id || '') : '';
    const hiredNext = next ? String(next.hired_by_architect_id || '') : '';
    if (focusedId && (hiredPrev === focusedId || hiredNext === focusedId)) {
      return true;
    }
  }
  if (focusedKind === 'engineer' || focusedKind === 'architect') {
    const ownerPrev = previous ? String(previous.owner_engineer_id || '') : '';
    const ownerNext = next ? String(next.owner_engineer_id || '') : '';
    if (focusedId && (ownerPrev === focusedId || ownerNext === focusedId)) {
      return true;
    }
  }
  return false;
}

function _architectPeerRosterDeltaInvalidatesFocusedMessages(previous, next, op) {
  const focused = _focusedEngineerAgent();
  if (!focused || _focusedEngineerAgentKind(focused) !== 'architect') return false;
  if (_focusedEngineerActiveTab('architect') !== 'messages') return false;
  const focusedGroup = String(focused.group || '');
  if (!focusedGroup) return false;
  if (op && (op.op === 'architect_dismissed' || op.op === 'architect_rehired')) {
    const architectId = String(op.architect_id || '');
    const architect = architectId && state && state.agents ? state.agents[architectId] : null;
    const group = String(op.group || (architect && architect.group) || '');
    return !!architectId && architectId !== String(focused.id || '') && group === focusedGroup;
  }
  const prevKind = String((previous && previous.kind) || '');
  const nextKind = String((next && next.kind) || '');
  if (prevKind !== 'architect' && nextKind !== 'architect') return false;
  const agentId = String((op && op.id) || (previous && previous.id) || (next && next.id) || '');
  if (agentId && agentId === String(focused.id || '')) return false;
  return _agentTouchesGroup(previous, next, focusedGroup);
}

function _applyAgentSurfaceInvalidation(flags, op, hint) {
  const previous = hint && hint.agent ? hint.agent : null;
  const next = _agentNextFromDelta(op, previous);
  if (_agentDeltaIsContextWindowOnly(previous, next, op)) {
    return;
  }
  if (_agentDeltaInvalidatesTerminalWorkspace(previous, next, op)) _markSurface(flags, 'terminal');
  if (!_standaloneDeltaOptimizationsEnabled()) {
    _markSurface(flags, 'main', 'context', 'events', 'engineer');
    if (_agentDeltaInvalidatesFocusPanel(previous, next, op)) _markSurface(flags, 'focus');
    return;
  }
  _markSurface(flags, 'main');
  if (_agentDeltaInvalidatesContext(previous, next, op)) _markSurface(flags, 'context');
  if (_agentDeltaInvalidatesEvents(previous, next, op)) _markSurface(flags, 'events');
  if (_agentDeltaInvalidatesFocusPanel(previous, next, op)) _markSurface(flags, 'focus');
  if (_agentDeltaInvalidatesEngineer(previous, next, op)) _markSurface(flags, 'engineer');
  if (_architectPeerRosterDeltaInvalidatesFocusedMessages(previous, next, op)) {
    _markSurface(flags, 'engineer');
  }
}

function _applyUiSurfaceInvalidation(flags, key) {
  if (key === 'standalone_panel_layout') {
    _markSurface(flags, 'board', 'chat', 'actions', 'context', 'events', 'engineer', 'templates', 'history', 'initiatives', 'thinking');
  }
  if (key === 'active_group') {
    _markSurface(flags, 'main', 'board', 'actions', 'context', 'events', 'engineer', 'templates', 'history', 'initiatives', 'thinking', 'terminal');
  }
  if (key === 'workspace_sidebar_width') {
    _markSurface(flags, 'main', 'board', 'chat', 'actions', 'context', 'events', 'engineer', 'templates', 'history', 'initiatives', 'thinking');
  }
  if (key === 'terminal_direct_messages_height') {
    _markSurface(flags, 'main', 'terminal');
  }
  if (key === 'terminal_compose_height') {
    _markSurface(flags, 'main', 'terminal');
  }
  if (key === 'context_panel_split_ratio') {
    _markSurface(flags, 'context');
  }
  if (key === 'supervisor_panel_state') {
    _markSurface(flags, 'supervisor');
  }
  if (key === 'events_dismissed_attention') {
    _markSurface(flags, 'events');
  }
  if (key === 'board_filters_by_group'
      || key === 'board_selected_lanes_by_group'
      || key === 'board_hidden_wide_lanes_by_group'
      || key === 'board_saved_views_by_group'
      || key === 'board_lane_sorts_by_group'
      || key === 'board_card_density_by_group') {
    _markSurface(flags, 'board');
  }
  if (key === 'selected_principal_id') {
    _markSurface(flags, 'main');
  }
  if (key === 'selected_agent_id') {
    if (_clientScopedFocusOwnsSelectedAgent()) return;
    _markSurface(flags, 'main', 'focus', 'context', 'events', 'engineer', 'terminal');
  }
}

function _collectSessionMapInvalidationGroups(ops, hints) {
  const groups = [];
  const seen = {};
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i] || {};
    const hint = hints && hints[i] ? hints[i] : {};
    let group = '';
    switch (op.op) {
      case 'agent_upsert':
      case 'task_upsert':
      case 'journal_append':
      case 'journal_delete':
      case 'agent_digest_update':
      case 'engineer_settings_update':
      case 'peer_message_upsert':
      case 'direct_message_upsert':
      case 'direct_message_read':
        group = op.group || '';
        break;
      case 'agent_remove':
      case 'task_remove':
        group = hint.group || '';
        break;
      default:
        group = '';
    }
    if (!group || seen[group]) continue;
    seen[group] = true;
    groups.push(group);
  }
  return groups;
}

function _surfaceUsesCurrentGroup(surface) {
  if (surface === 'board') {
    return typeof _boardFilterByGroup === 'undefined' || !!_boardFilterByGroup;
  }
  if (surface === 'events') {
    return typeof _eventsFilterByGroup === 'undefined' || !!_eventsFilterByGroup;
  }
  return surface === 'context' || surface === 'engineer' || surface === 'initiatives' || surface === 'thinking';
}

function _captureDeltaGroupHints(ops) {
  const hints = [];
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i] || {};
    let group = '';
    let task = null;
    let agent = null;
    if (op.op === 'agent_remove' && state && state.agents && state.agents[op.id]) {
      group = state.agents[op.id].group || '';
      agent = _cloneBoardDeltaTask(state.agents[op.id]);
    } else if (op.op === 'agent_upsert' && state && state.agents && state.agents[op.id]) {
      group = state.agents[op.id].group || '';
      agent = _cloneBoardDeltaTask(state.agents[op.id]);
    } else if ((op.op === 'task_remove' || op.op === 'task_upsert')
        && state && state.board_tasks && state.board_tasks[op.id]) {
      group = state.board_tasks[op.id].group || '';
      task = _cloneBoardDeltaTask(state.board_tasks[op.id]);
    }
    hints.push({ group: group, task: task, agent: agent });
  }
  return hints;
}

function _cloneBoardDeltaTask(task) {
  if (!task) return null;
  return Object.assign({}, task);
}

function _deltaOpsAreOnlyTaskDeltas(ops) {
  if (!ops || !ops.length) return false;
  for (let i = 0; i < ops.length; i++) {
    const opName = (ops[i] && ops[i].op) || '';
    if (opName !== 'task_upsert' && opName !== 'task_remove') return false;
  }
  return true;
}

function _collectBoardTaskDeltaChanges(ops, hints) {
  const changes = [];
  for (let i = 0; i < (ops || []).length; i++) {
    const op = ops[i] || {};
    if (op.op !== 'task_upsert' && op.op !== 'task_remove') continue;
    const id = op.id || '';
    const hint = hints && hints[i] ? hints[i] : {};
    const previous = hint.task || null;
    const next = (op.op === 'task_remove' || !state || !state.board_tasks)
      ? null
      : _cloneBoardDeltaTask(state.board_tasks[id]);
    changes.push({
      op: op.op,
      id: id,
      previous: previous,
      next: next,
    });
  }
  return changes;
}

function _collectBoardAgentDeltaChanges(ops, hints) {
  const changes = [];
  for (let i = 0; i < (ops || []).length; i++) {
    const op = ops[i] || {};
    if (op.op !== 'agent_upsert' && op.op !== 'agent_remove') continue;
    const id = op.id || '';
    const hint = hints && hints[i] ? hints[i] : {};
    const previous = hint.agent || null;
    const next = (op.op === 'agent_remove' || !state || !state.agents)
      ? null
      : _cloneBoardDeltaTask(state.agents[id]);
    changes.push({
      op: op.op,
      id: id,
      previous: previous,
      next: next,
    });
  }
  return changes;
}

function _opTouchesGroup(op, group, hint) {
  if (!op || !group) return true;
  const hintedGroup = (hint && hint.group) ? hint.group : '';
  switch (op.op) {
    case 'agent_upsert':
    case 'task_upsert':
    case 'event_append':
    case 'mcp_call_append':
      return (op.group || '') === group || (!!hintedGroup && hintedGroup === group);
    case 'agent_remove':
    case 'task_remove':
      return hintedGroup ? hintedGroup === group : true;
    case 'group_update':
    case 'group_remove':
    case 'group_settings_update':
      return (op.name || '') === group;
    case 'group_rename':
      return op.old_name === group || op.new_name === group;
    case 'journal_append':
    case 'journal_delete':
    case 'agent_digest_update':
    case 'digest_buffer_stats':
    case 'digest_sent_push':
    case 'engineer_buffer_stats':
    case 'engineer_sent_events':
    case 'engineer_worklog_append':
    case 'engineer_streams':
    case 'engineer_streams_update':
    case 'engineer_settings_update':
    case 'peer_message_upsert':
    case 'direct_message_upsert':
    case 'direct_message_read':
    case 'thinking_scratchpad_note_upsert':
    case 'idea_brief_upsert':
      return (op.group || op.group_name || '') === group;
    default:
      return true;
  }
}

function _opsAffectCurrentSurfaceGroup(surface, group, ops, hints) {
  if (!surface || !_surfaceUsesCurrentGroup(surface) || !group) return true;
  for (let i = 0; i < ops.length; i++) {
    if (_opTouchesGroup(ops[i], group, hints && hints[i])) return true;
  }
  return false;
}

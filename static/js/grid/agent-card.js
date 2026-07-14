function _agentCardCompactRelativeTime(value) {
  const ts = _agentCardTimestampSeconds(value);
  if (!ts) return '—';
  const diff = Math.max(0, Math.floor((Date.now() / 1000) - ts));
  if (diff < 60) return String(diff || 0) + 's';
  if (diff < 3600) return Math.floor(diff / 60) + 'm';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h';
  return Math.floor(diff / 86400) + 'd';
}

function _truncateCardText(text, maxChars) {
  const raw = String(text || '').trim();
  const max = Math.max(1, Number(maxChars || 0) || 14);
  if (raw.length <= max) return raw;
  if (max <= 1) return '…';
  return raw.slice(0, max - 1) + '…';
}

function _agentCardTooltipHtml(text, maxChars, className, attrs) {
  const raw = String(text || '').trim();
  const visible = _truncateCardText(raw, maxChars);
  const contentClasses = ['agent-card-trunc'];
  if (className) contentClasses.push(className);
  const extraAttrs = attrs ? ' ' + attrs : '';
  return '<span class="agent-card-tooltip"'
    + ' data-tooltip="' + esc(raw) + '"'
    + ' aria-label="' + esc(raw) + '"'
    + extraAttrs + '>'
    + '<span class="' + esc(contentClasses.join(' ')) + '">'
    + esc(visible)
    + '</span>'
    + '</span>';
}

function _agentCardLatestMcpMessage(agent) {
  const messages = agent && Array.isArray(agent.mcp_messages)
    ? agent.mcp_messages : [];
  let latest = null;
  let latestTs = 0;
  for (let i = 0; i < messages.length; i++) {
    const entry = messages[i] || {};
    const ts = _agentCardTimestampSeconds(entry.timestamp);
    if (!latest || ts > latestTs || (ts === latestTs && i === 0)) {
      latest = entry;
      latestTs = ts;
    }
  }
  return latest ? { entry: latest, timestamp: latestTs } : null;
}

var _AGENT_CARD_TOOL_LABELS = {
  torque_context: 'Checking context',
  torque_progress: 'Reporting progress',
  torque_done: 'Completing task',
  torque_ready: 'Completing task',
  torque_derive: 'Deriving subtask',
  torque_ask: 'Asking',
  torque_blocked: 'Reporting blocker',
  torque_error: 'Reporting error',
  torque_message_user: 'Messaging user',
  torque_verify: 'Verifying',
  torque_memory_publish: 'Publishing memory',
  torque_memory_pin: 'Pinning memory',
  torque_memory_link: 'Linking memory',

  architect_journal: 'Journaling',
  architect_journal_read: 'Reading journal',
  architect_engineer_journal_read: 'Reading engineer journal',
  architect_message_engineer: 'Messaging engineer',
  architect_message_user: 'Messaging user',
  architect_reply: 'Replying',
  architect_peer_message: 'Messaging peer architect',
  architect_peer_inbox: 'Checking peer inbox',
  architect_engineer_peer_threads: 'Inspecting engineer peer threads',
  architect_engineer_peer_inspect: 'Inspecting engineer peer thread',
  architect_ask: 'Asking',
  architect_engineer_list: 'Reviewing engineers',
  architect_engineer_hire: 'Hiring engineer',
  architect_engineer_dismiss: 'Dismissing engineer',
  architect_engineer_rehire: 'Rehiring engineer',
  architect_engineer_restore: 'Restoring engineer',
  architect_decision_create: 'Creating decision',
  architect_decision_update: 'Updating decision',
  architect_decision_link: 'Linking decision',
  architect_decision_list: 'Reviewing decisions',
  architect_task_create: 'Creating task',
  architect_task_pickup: 'Picking up task',
  architect_task_update: 'Updating task',
  architect_task_mark_covered: 'Marking covered',
  architect_task_dispatch: 'Dispatching',
  architect_batch_dispatch: 'Dispatching batch',
  architect_task_move: 'Moving task',
  architect_task_show: 'Reviewing task',
  architect_task_list: 'Reviewing tasks',
  architect_task_reassign: 'Reassigning task',
  architect_task_edit: 'Editing task',
  architect_task_upload_artifact: 'Uploading artifact',
  architect_task_verify: 'Verifying task',
  architect_task_resolve: 'Resolving task',
  architect_board_summary: 'Reviewing board',
  architect_board_list: 'Reviewing board',
  architect_events: 'Reviewing events',
  architect_events_recent: 'Reviewing events',
  architect_mcp_calls: 'Reviewing MCP calls',
  architect_session_map: 'Reviewing sessions',
  architect_agent_show: 'Inspecting agent',
  architect_agents_list: 'Reviewing agents',
  architect_actions_list: 'Reviewing actions',
  architect_action_show: 'Reviewing action',
  architect_specializations_list: 'Reviewing specializations',
  architect_specialization_show: 'Reviewing specialization',
  architect_specialization_save: 'Saving specialization',
  architect_specialization_delete: 'Deleting specialization',
  architect_streams_list: 'Reviewing streams',
  architect_stream_show: 'Reviewing stream',
  architect_deploy_state: 'Checking deploy state',
  architect_get_architect_settings: 'Checking architect settings',
  architect_task_chain: 'Reviewing task chain',
  architect_pending_hire_status: 'Checking hire status',
  architect_pending_hire_list: 'Reviewing pending hires',
  architect_notifications: 'Checking notifications',
  architect_resume: 'Resuming',
  architect_note: 'Recording note',
  architect_agent_message: 'Messaging agent',
  architect_agent_close: 'Closing agent',

  engineer_journal: 'Journaling',
  engineer_journal_read: 'Reading journal',
  engineer_task_dispatch: 'Dispatching',
  engineer_batch_dispatch: 'Dispatching batch',
  engineer_task_create: 'Creating task',
  engineer_task_update: 'Updating task',
  engineer_task_mark_covered: 'Marking covered',
  engineer_task_move: 'Moving task',
  engineer_task_show: 'Reviewing task',
  engineer_task_list: 'Reviewing tasks',
  engineer_task_reassign: 'Reassigning task',
  engineer_task_edit: 'Editing task',
  engineer_task_upload_artifact: 'Uploading artifact',
  engineer_task_verify: 'Verifying task',
  engineer_task_resolve: 'Resolving task',
  engineer_message_architect: 'Messaging architect',
  engineer_message_user: 'Messaging user',
  engineer_reply: 'Replying',
  engineer_peer_notify: 'Notifying peer engineer',
  engineer_peer_reply: 'Replying to peer engineer',
  engineer_peer_inbox: 'Checking peer engineer inbox',
  engineer_peer_inspect: 'Inspecting peer context',
  engineer_ask: 'Asking',
  engineer_pending_question: 'Checking question',
  engineer_answer: 'Answering question',
  engineer_diff: 'Reviewing diff',
  engineer_merge: 'Merging',
  engineer_session_map: 'Reviewing sessions',
  engineer_events: 'Reviewing events',
  engineer_events_recent: 'Reviewing events',
  engineer_notifications: 'Checking notifications',
  engineer_board_summary: 'Reviewing board',
  engineer_board_list: 'Reviewing board',
  engineer_agents_list: 'Reviewing agents',
  engineer_agent_show: 'Inspecting agent',
  engineer_actions_list: 'Reviewing actions',
  engineer_action_show: 'Reviewing action',
  engineer_specializations_list: 'Reviewing specializations',
  engineer_specialization_show: 'Reviewing specialization',
  engineer_streams_list: 'Reviewing streams',
  engineer_stream_show: 'Reviewing stream',
  engineer_mcp_calls: 'Reviewing MCP calls',
  engineer_deploy_state: 'Checking deploy state',
  engineer_launch_settings: 'Checking launch settings',
  engineer_task_chain: 'Reviewing task chain',
  engineer_note: 'Recording note',
  engineer_agent_message: 'Messaging agent',
  engineer_agent_close: 'Closing agent',

  journal: 'Journaling',
  journal_read: 'Reading journal',
  task_dispatch: 'Dispatching',
  batch_dispatch: 'Dispatching batch',
  task_create: 'Creating task',
  task_update: 'Updating task',
  task_move: 'Moving task',
  task_show: 'Reviewing task',
  task_list: 'Reviewing tasks',
  task_reassign: 'Reassigning task',
  task_edit: 'Editing task',
  task_upload_artifact: 'Uploading artifact',
  task_verify: 'Verifying task',
  task_resolve: 'Resolving task',
  message_user: 'Messaging user',
  reply: 'Replying',
  ask: 'Asking',
  note: 'Recording note',
  diff: 'Reviewing diff',
  merge: 'Merging',
  board_summary: 'Reviewing board',
  board_list: 'Reviewing board',
  events: 'Reviewing events',
  events_recent: 'Reviewing events',
  mcp_calls: 'Reviewing MCP calls',
  session_map: 'Reviewing sessions',
  agent_show: 'Inspecting agent',
  agents_list: 'Reviewing agents',
  notifications: 'Checking notifications',
  resume: 'Resuming',

  'claude-in-chrome:navigate': 'Navigating',
  'claude-in-chrome:click': 'Clicking',
  'claude-in-chrome:type': 'Typing',
  'claude-in-chrome:press_key': 'Pressing key',
  'claude-in-chrome:screenshot': 'Taking screenshot',
  'claude-in-chrome:get_page': 'Reading page',
  'claude-in-chrome:get_page_snapshot': 'Reading page',
  'claude-in-chrome:get_console_logs': 'Reading console logs',
  'claude-in-chrome:select_tab': 'Selecting tab',
};

function _mcpToolParts(tool) {
  const value = String(tool || '').trim();
  const match = /^mcp__(.+?)__(.+)$/.exec(value);
  if (match) {
    return {
      raw: value,
      server: match[1],
      name: match[2],
      isMcp: true,
    };
  }
  return {
    raw: value,
    server: '',
    name: value,
    isMcp: false,
  };
}

function _stripFriendlyToolPrefix(name) {
  return String(name || '').trim().replace(/^(torque|engineer|architect)_/, '');
}

function _prettifyToolName(name) {
  const value = _stripFriendlyToolPrefix(name)
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!value) return '';
  return value.charAt(0).toUpperCase() + value.slice(1).toLowerCase();
}

function _agentCardLooksLikeToolIdentifier(text) {
  const value = String(text || '').trim();
  if (!value) return false;
  if (/^mcp__.+?__.+$/.test(value)) return true;
  if (/^(torque|engineer|architect)_[a-z0-9_]+$/i.test(value)) return true;
  if (Object.prototype.hasOwnProperty.call(_AGENT_CARD_TOOL_LABELS, value)) return true;
  return false;
}

function _humanizeBareToolLabel(tool) {
  const parts = _mcpToolParts(tool);
  const name = String(parts.name || '').trim();
  if (!name) return '';
  const serverKey = parts.server ? parts.server + ':' + name : '';
  if (serverKey && Object.prototype.hasOwnProperty.call(_AGENT_CARD_TOOL_LABELS, serverKey)) {
    return _AGENT_CARD_TOOL_LABELS[serverKey];
  }
  if (Object.prototype.hasOwnProperty.call(_AGENT_CARD_TOOL_LABELS, name)) {
    return _AGENT_CARD_TOOL_LABELS[name];
  }
  const stripped = _stripFriendlyToolPrefix(name);
  const strippedServerKey = parts.server ? parts.server + ':' + stripped : '';
  if (strippedServerKey && Object.prototype.hasOwnProperty.call(_AGENT_CARD_TOOL_LABELS, strippedServerKey)) {
    return _AGENT_CARD_TOOL_LABELS[strippedServerKey];
  }
  if (Object.prototype.hasOwnProperty.call(_AGENT_CARD_TOOL_LABELS, stripped)) {
    return _AGENT_CARD_TOOL_LABELS[stripped];
  }
  return _prettifyToolName(name);
}

function _humanizeToolLabel(tool) {
  const value = String(tool || '').trim();
  if (!value) return '';

  const usingMatch = /^Using\s+(.+)$/.exec(value);
  if (usingMatch) {
    const wrapped = usingMatch[1].trim();
    return _agentCardLooksLikeToolIdentifier(wrapped)
      ? _humanizeToolLabel(wrapped)
      : value;
  }

  const endMatch = /^(.+?)\s+(finished|failed)$/i.exec(value);
  if (endMatch && _agentCardLooksLikeToolIdentifier(endMatch[1])) {
    const label = _humanizeToolLabel(endMatch[1]);
    return endMatch[2].toLowerCase() === 'failed'
      ? label + ' failed'
      : label;
  }

  if (!_agentCardLooksLikeToolIdentifier(value)) return value;
  return _humanizeBareToolLabel(value) || value;
}

function _agentProviderMeta(provider) {
  const key = String(provider || '').trim().toLowerCase();
  if (key === 'claude-code') {
    return { key, label: 'Claude', cls: 'agent-card-provider--claude-code' };
  }
  if (key === 'codex') {
    return { key, label: 'Codex', cls: 'agent-card-provider--codex' };
  }
  if (!key || key === 'generic') return null;
  return { key, label: key.replace(/[-_]+/g, ' '), cls: 'agent-card-provider--unknown' };
}

function _renderAgentProviderBadge(provider, extraClass) {
  const meta = _agentProviderMeta(provider);
  if (!meta) return '';
  const classes = ['agent-card-provider', meta.cls];
  if (extraClass) classes.push(extraClass);
  return '<span class="' + esc(classes.join(' ')) + '"'
    + ' data-provider="' + esc(meta.key) + '">'
    + esc(meta.label)
    + '</span>';
}

function _agentContextWindowInfo(agentOrContext) {
  const context = agentOrContext && agentOrContext.context_window !== undefined
    ? agentOrContext.context_window
    : agentOrContext;
  if (!context || typeof context !== 'object') return null;
  if (Object.keys(context).length === 0) return null;

  let pct = Number(context.used_pct);
  if (!Number.isFinite(pct)) {
    const used = Number(context.used_tokens);
    const limit = Number(context.limit_tokens);
    if (Number.isFinite(used) && Number.isFinite(limit) && limit > 0) {
      pct = (used / limit) * 100;
    }
  }
  if (!Number.isFinite(pct)) return null;

  const displayPct = Math.max(0, Math.round(pct));
  let level = 'normal';
  if (displayPct >= 90) level = 'danger';
  else if (displayPct >= 70) level = 'warn';
  // At the 3-digit (>=100%) case the "ctx " prefix overflows narrow 77px cards
  // by ~1-2px; drop the prefix there so "100%" fits cleanly while every other
  // value keeps the labelled "ctx NN%" form.
  const label = displayPct >= 100 ? displayPct + '%' : 'ctx ' + displayPct + '%';
  return {
    pct,
    displayPct,
    level,
    label,
  };
}

function _agentContextMeterClasses(info) {
  const level = info && info.level ? info.level : 'normal';
  return [
    'agent-context-meter',
    'agent-context-meter--' + level,
  ];
}

function _renderAgentContextMeter(agent) {
  const info = _agentContextWindowInfo(agent);
  if (!info) return '';
  const classes = _agentContextMeterClasses(info);
  return '<div class="' + esc(classes.join(' ')) + '"'
    + ' data-agent-context-meter'
    + ' data-context-level="' + esc(info.level) + '"'
    + ' data-context-pct="' + esc(String(info.displayPct)) + '">'
    + esc(info.label)
    + '</div>';
}

function _agentGridCardSelector(agentId) {
  const raw = String(agentId || '');
  if (!raw) return '';
  const escaped = (typeof CSS !== 'undefined' && CSS && typeof CSS.escape === 'function')
    ? CSS.escape(raw)
    : raw.replace(/["\\]/g, '\\$&');
  return '[data-drag-id="' + escaped + '"][data-drag-type="agent"]';
}

function _findAgentGridCard(agentId) {
  const selector = _agentGridCardSelector(agentId);
  if (!selector || typeof document === 'undefined' || !document.querySelector) return null;
  return document.querySelector(selector);
}

function _applyAgentContextMeterElement(el, info) {
  if (!el || !info) return;
  const classes = _agentContextMeterClasses(info).join(' ');
  if ('className' in el) el.className = classes;
  if (typeof el.setAttribute === 'function') {
    el.setAttribute('class', classes);
    el.setAttribute('data-context-level', info.level);
    el.setAttribute('data-context-pct', String(info.displayPct));
    el.setAttribute('data-agent-context-meter', '');
  }
  if (el.dataset) {
    el.dataset.contextLevel = info.level;
    el.dataset.contextPct = String(info.displayPct);
  }
  el.textContent = info.label;
}

function _createAgentContextMeterElement(info) {
  if (typeof document === 'undefined' || !document.createElement || !info) return null;
  const el = document.createElement('div');
  _applyAgentContextMeterElement(el, info);
  return el;
}

function _invalidateAgentGridMemoAfterCardSurgery() {
  const main = (typeof document !== 'undefined' && document.getElementById)
    ? document.getElementById('main')
    : null;
  if (!main) return;
  // The grid shell memoizes the HTML fragment. A surgical child mutation must
  // invalidate those snapshots so the next ordinary render reconciles against
  // state instead of trusting a pre-surgery string cache.
  main._torqueLastGridHtml = null;
  main._torqueLastHtml = null;
}

function updateAgentContextMeter(agentId) {
  const id = String(agentId || '').trim();
  if (!id || !state || !state.agents) return false;
  const agent = state.agents[id];
  if (!agent) return false;
  const card = _findAgentGridCard(id);
  if (!card || !card.querySelector) return false;
  const existing = card.querySelector('[data-agent-context-meter]');
  const info = _agentContextWindowInfo(agent);

  if (!info) {
    if (!existing) return false;
    if (typeof existing.remove === 'function') existing.remove();
    else if (existing.parentNode && Array.isArray(existing.parentNode.children)) {
      existing.parentNode.children = existing.parentNode.children.filter(function(child) {
        return child !== existing;
      });
      existing.parentNode = null;
    }
    _invalidateAgentGridMemoAfterCardSurgery();
    return true;
  }

  if (existing) {
    const nextText = info.label;
    const nextLevel = info.level;
    const nextPct = String(info.displayPct);
    const same = existing.textContent === nextText
      && (!existing.dataset || (
        existing.dataset.contextLevel === nextLevel
        && existing.dataset.contextPct === nextPct
      ));
    if (same) return false;
    _applyAgentContextMeterElement(existing, info);
    _invalidateAgentGridMemoAfterCardSurgery();
    return true;
  }

  if (typeof card.appendChild !== 'function') return false;
  const el = _createAgentContextMeterElement(info);
  if (!el) return false;
  card.appendChild(el);
  _invalidateAgentGridMemoAfterCardSurgery();
  return true;
}

function _agentKindBadgeLabel(kind, dismissed) {
  if (dismissed) return 'Dismissed';
  if (kind === 'architect') return 'Architect';
  if (kind === 'engineer') return 'Engineer';
  if (kind === 'worker') return 'Worker';
  return 'Agent';
}

function _agentCardDefaultClassId(kind) {
  kind = String(kind || '').trim();
  if (kind === 'architect' || kind === 'engineer' || kind === 'worker') return 'default-' + kind;
  return '';
}

function _agentCardKindDisplayLabel(kind) {
  return _agentKindBadgeLabel(kind, false);
}

function _agentCardEffectiveClassSnapshot(agent) {
  if (agent && agent.effective_agent_class_snapshot
      && typeof agent.effective_agent_class_snapshot === 'object') {
    return agent.effective_agent_class_snapshot;
  }
  const status = agent && agent.agent_class_status && typeof agent.agent_class_status === 'object'
    ? agent.agent_class_status
    : {};
  return status.effective_class && typeof status.effective_class === 'object'
    ? status.effective_class
    : {};
}

function _agentCardClassDisplayLabel(id, snapshot, status, rawLabel) {
  return String(rawLabel || '').trim();
}

function _agentCardPrimaryClassIdentity(agent) {
  if (!agent || String(agent.cell_type || 'agent') !== 'agent') return null;
  const kind = String(agent.kind || '').trim();
  const defaultId = _agentCardDefaultClassId(kind);
  const snapshot = _agentCardEffectiveClassSnapshot(agent);
  const status = agent.agent_class_status && typeof agent.agent_class_status === 'object'
    ? agent.agent_class_status
    : {};
  const id = String(
    agent.effective_agent_class_id
    || snapshot.id
    || status.effective_class_id
    || ''
  ).trim();
  if (!id || id === defaultId) return null;
  const snapshotLabel = String(
    snapshot.primary_identity_label
    || snapshot.primary_display_name
    || snapshot.display_name
    || ''
  ).trim();
  const rawLabel = String(
    snapshotLabel
    || status.effective_primary_identity_label
    || status.primary_identity_label
    || id
  ).trim();
  const label = _agentCardClassDisplayLabel(id, snapshot, status, rawLabel);
  if (!label) return null;
  return {
    id,
    label,
    version: String(agent.effective_agent_class_version || snapshot.version || status.effective_class_version || '').trim(),
    baseKind: kind,
    baseKindLabel: _agentCardKindDisplayLabel(kind),
    secondary: String(
      status.secondary_base_kind_label
      || snapshot.secondary_base_kind_label
      || (snapshot.secondary_base_kind_metadata && snapshot.secondary_base_kind_metadata.base_kind_label)
      || _agentCardKindDisplayLabel(kind)
    ).trim(),
    status: String(snapshot.status || snapshot.lifecycle || status.status || '').trim() || 'full',
  };
}

function _agentCardPrimaryDisplayName(agent) {
  return _agentDisplayName(agent);
}

function _agentKindBadgeClass(kind, dismissed) {
  if (dismissed) return 'cell-dismissed-badge';
  if (kind === 'architect') return 'cell-architect-badge';
  if (kind === 'engineer') return 'cell-engineer-badge';
  if (kind === 'worker') return 'cell-worker-badge';
  return 'cell-agent-badge';
}

function _agentCardKindBadge(agent, dismissed) {
  const kind = String((agent && agent.kind) || '');
  const identity = _agentCardPrimaryClassIdentity(agent);
  if (identity && !dismissed) {
    return {
      label: identity.label,
      cls: _agentKindBadgeClass(kind, dismissed) + ' cell-agent-class-badge',
      title: 'Agent Class: ' + identity.label
        + (identity.version ? ('@' + identity.version) : '')
        + '\nBase kind: ' + (identity.baseKindLabel || kind || 'agent')
        + '\nSecondary metadata: ' + (identity.secondary || '—')
        + '\nStatus: ' + (identity.status || 'full'),
    };
  }
  return {
    label: _agentKindBadgeLabel(kind, dismissed),
    cls: _agentKindBadgeClass(kind, dismissed),
    title: dismissed ? ('Dismissed ' + (kind || 'agent')) : '',
  };
}

function _agentDisplayName(agent) {
  if (!agent) return '';
  return agent.name || agent.slug || agent.id || '';
}

function _agentCardLastActionValue(agent) {
  if (!agent) return 0;
  const latestMcp = _agentCardLatestMcpMessage(agent);
  return Math.max(
    _agentCardTimestampSeconds(agent.last_action_at),
    latestMcp ? latestMcp.timestamp : 0,
    _agentCardTimestampSeconds(agent.last_progress_at),
    _agentCardTimestampSeconds(agent.last_event_at),
    _agentCardTimestampSeconds(agent.last_activity_at),
    _agentCardTimestampSeconds(agent.last_heartbeat_at),
    _agentCardTimestampSeconds(agent.updated_at),
    _agentCardTimestampSeconds(agent.created_at),
  );
}

function _agentCardActionTextFromMcp(entry) {
  if (!entry) return '';
  const action = String(entry.action || '').trim();
  const message = String(entry.message || '').trim();
  const rawToolText = String(entry.tool || entry.tool_name || entry.name || '').trim();
  const toolText = _agentCardLooksLikeToolIdentifier(rawToolText)
    ? _humanizeToolLabel(rawToolText)
    : '';
  if (action === 'progress' && message) return _humanizeToolLabel(message);
  if (action === 'derive') {
    if (message) return _humanizeToolLabel(message);
    return 'derived follow-up';
  }
  if (action === 'verify') return message ? _humanizeToolLabel(message) : (toolText || 'verified');
  if (action === 'ask') return message ? _humanizeToolLabel(message) : (toolText || 'asked for input');
  if (action === 'blocked') return message ? _humanizeToolLabel(message) : (toolText || 'blocked');
  if (action === 'error') return message ? _humanizeToolLabel(message) : (toolText || 'error');
  if (action === 'done') return message && message !== 'Done' ? _humanizeToolLabel(message) : (toolText || 'done');
  if (action === 'ready') return message && message !== 'Ready' ? _humanizeToolLabel(message) : (toolText || 'ready');
  return _humanizeToolLabel(message || toolText || action);
}

function _agentCardFallbackActionText(agent) {
  if (!agent) return '—';
  const activity = String(agent.activity || '').trim();
  if (activity === 'tool_call') return 'working';
  if (activity === 'waiting') return 'waiting';
  if (activity === 'thinking' || activity === 'writing') return 'working';
  const status = String(agent.status || '').trim();
  if (status === 'running') return 'working';
  if (agent.needs_attention) return 'needs attention';
  return 'idle';
}

function _agentCardActionCandidate(text, timestamp, priority) {
  const value = _humanizeToolLabel(text);
  if (!value) return null;
  return {
    text: value,
    timestamp: _agentCardTimestampSeconds(timestamp),
    priority: Number(priority || 0) || 0,
  };
}

function _agentCardEventTimestamp(agent) {
  if (!agent) return 0;
  return Math.max(
    _agentCardTimestampSeconds(agent.last_event_at),
    _agentCardTimestampSeconds(agent.last_activity_at),
    _agentCardTimestampSeconds(agent.last_heartbeat_at),
  );
}

function _agentCardActivityDetailTimestamp(agent) {
  if (!agent) return 0;
  const activityDetail = String(agent.activity_detail || '').trim();
  if (!activityDetail) return 0;
  const eventText = String(agent.last_event_text || '').trim();
  let timestamp = Math.max(
    _agentCardTimestampSeconds(agent.last_action_at),
    _agentCardTimestampSeconds(agent.last_progress_at),
  );
  /* activity_detail is advanced by progress reports, while last_event_text
     can be advanced independently by heartbeat-style events such as
     queue_empty. Only borrow the event clock when the event text is the same
     detail (or when there is no competing event text); otherwise an old
     activity_detail would hide a newer queue_empty display. */
  if (!eventText || eventText === activityDetail) {
    timestamp = Math.max(timestamp, _agentCardEventTimestamp(agent));
  }
  return timestamp;
}

function _agentCardFallbackActionTimestamp(agent, latestMcp) {
  if (!agent) return 0;
  return Math.max(
    _agentCardTimestampSeconds(agent.last_action_at),
    latestMcp ? latestMcp.timestamp : 0,
    _agentCardTimestampSeconds(agent.last_progress_at),
    _agentCardEventTimestamp(agent),
  );
}

function _agentCardNewestActionCandidate(candidates) {
  const list = (Array.isArray(candidates) ? candidates : []).filter(Boolean);
  if (!list.length) return null;
  list.sort((a, b) => {
    const aTs = _agentCardTimestampSeconds(a.timestamp);
    const bTs = _agentCardTimestampSeconds(b.timestamp);
    if (aTs !== bTs) return bTs - aTs;
    return (Number(b.priority || 0) || 0) - (Number(a.priority || 0) || 0);
  });
  return list[0];
}

function _agentCardLastActionInfo(agent) {
  const latestMcp = _agentCardLatestMcpMessage(agent);
  const candidates = [];
  if (agent) {
    candidates.push(_agentCardActionCandidate(
      agent.activity_detail,
      _agentCardActivityDetailTimestamp(agent),
      30,
    ));
    if (latestMcp) {
      candidates.push(_agentCardActionCandidate(
        _agentCardActionTextFromMcp(latestMcp.entry),
        latestMcp.timestamp,
        20,
      ));
    }
    candidates.push(_agentCardActionCandidate(
      agent.last_event_text,
      _agentCardEventTimestamp(agent),
      10,
    ));
    candidates.push(_agentCardActionCandidate(
      _agentCardFallbackActionText(agent),
      _agentCardFallbackActionTimestamp(agent, latestMcp),
      0,
    ));
  }
  return _agentCardNewestActionCandidate(candidates) || {
    text: _agentCardFallbackActionText(agent),
    timestamp: _agentCardTimestampSeconds(_agentCardLastActionValue(agent)),
  };
}

function _agentCardCurrentOrLastActionLabel(agent) {
  const info = _agentCardLastActionInfo(agent);
  // The status dot already conveys idle state — don't surface the literal
  // "idle" text in the action line. Returning empty leaves the line slot in
  // place (preserves uniform card height) but renders no content.
  if (info.text === 'idle') return '';
  if (!info.timestamp) return info.text;
  const age = Math.max(0, Math.floor((Date.now() / 1000) - info.timestamp));
  if (age < 60) return info.text;
  return info.text + ' (' + _agentCardCompactRelativeTime(info.timestamp) + ')';
}
function _agentTaskForCard(agent) {
  if (!agent) return null;
  const currentTaskId = String(agent.current_task_id || '').trim();
  if (currentTaskId && state && state.board_tasks && state.board_tasks[currentTaskId]) {
    return state.board_tasks[currentTaskId];
  }
  return _getAgentTask(agent.id);
}

function _taskCycleState(task, agent) {
  if (agent && (agent.needs_attention || agent.error_message)) return 'blocked';
  if (!task) return 'idle';
  const lane = String(task.lane || '').trim().toLowerCase();
  const status = String(task.status || '').trim().toLowerCase();
  const health = String(task.health_state || '').trim().toLowerCase();
  const action = String(task.action_name || task.suggested_action || '').trim().toLowerCase();
  const actionTail = action.split('/').filter(Boolean).pop() || action;
  if (status.indexOf('block') >= 0 || health.indexOf('block') >= 0) return 'blocked';
  if (lane === 'done') return 'done';
  if (action.indexOf('review') >= 0) return 'review';
  if (action.indexOf('fix') >= 0) return 'fix';
  if (action.indexOf('implement') >= 0 || actionTail === 'impl') return 'implementation';
  if (action) {
    return actionTail.replace(/[-_]+/g, ' ');
  }
  if (lane === 'in progress') return 'in progress';
  if (lane) return lane.replace(/[-_]+/g, ' ');
  return 'idle';
}
function _agentStatusMixClass(agent) {
  const cls = typeof agentStatusClass === 'function' ? agentStatusClass(agent) : '';
  if (cls === 'attention' || cls === 'disconnected') return 'error';
  if (cls === 'working') return 'running';
  return 'idle';
}

function _agentStatusMixDots(agents) {
  const list = Array.isArray(agents) ? agents : [];
  if (!list.length) {
    return '<span class="agent-card-state-dot agent-card-state-dot--empty"></span>';
  }
  const shown = list.slice(0, 3);
  let html = '';
  for (const agent of shown) {
    const mix = _agentStatusMixClass(agent);
    html += '<span class="agent-card-state-dot agent-card-state-dot--' + esc(mix) + '"></span>';
  }
  if (list.length > shown.length) {
    html += '<span class="agent-card-state-more">+' + esc(list.length - shown.length) + '</span>';
  }
  return html;
}
function _architectStatsForCard(architect, section) {
  const engineers = _architectEngineersForCard(architect && architect.id, section);
  const asks = _architectPendingAskTasks(architect);
  const decisions = _architectDecisionListForCard(architect && architect.id);
  const journalDecisions = _architectJournalDecisionEntriesForCard(architect && architect.id);
  let decisionCount = 0;
  let latestDecisionTs = 0;
  for (const decision of decisions) {
    if (!decision.archived) decisionCount += 1;
    latestDecisionTs = Math.max(
      latestDecisionTs,
      _agentCardTimestampSeconds((decision && (decision.updated_at || decision.created_at)) || 0)
    );
  }
  latestDecisionTs = Math.max(
    latestDecisionTs,
    _architectLatestJournalDecisionTs(architect && architect.id)
  );
  return {
    engineerCount: engineers.length,
    asks,
    askCount: asks.length,
    firstAskId: asks.length ? String(asks[0].id || '') : '',
    decisionCount: decisionCount + journalDecisions.length,
    latestDecisionTs,
  };
}
function _agentCellSubtitle(a) {
  const task = _getAgentTask(a.id);
  if (task && task.task) return task.task;
  if (!_embeddedRuntimeEnabled()) return '';
  return a.activity_detail
    || _formatDisplayPath(
      a.current_path || a.directory || '',
      a.git_root || a.worktree_repo_root || ''
    )
    || a.command
    || '';
}

function _renderAgentCardControls(a, opts) {
  opts = opts || {};
  const id = String((a && a.id) || '');
  const closeTitle = opts.dismissed ? 'Delete dismissed engineer' : 'Delete';
  const paused = !!opts.paused;
  const pauseTitle = opts.pauseTitle || (paused ? 'Resume event delivery' : 'Pause event delivery');
  const pauseIcon = paused ? '&#x25B6;' : '&#x23F8;';
  let html = '<div class="cell-header-controls">';
  html += '<button class="cell-close" draggable="false"'
    + ' data-focus-key="agent-close:' + esc(id) + '"'
    + ' onclick="event.stopPropagation();removeAgent(\'' + esc(id) + '\')"'
    + ' title="' + esc(closeTitle) + '">\u2715</button>';
  html += '<button class="cell-engineer-toggle ' + (paused ? 'paused' : 'running') + '"'
    + ' draggable="false"'
    + ' data-focus-key="agent-digest-toggle:' + esc(id) + '"'
    + ' onclick="' + esc(opts.pauseOnclick || '') + '"'
    + ' title="' + esc(pauseTitle) + '">' + pauseIcon + '</button>';
  html += '</div>';
  return html;
}

function _renderWorkerCardBody(a) {
  const task = _agentTaskForCard(a);
  const taskId = String((task && task.id) || a.current_task_id || '').trim();
  const cycle = _taskCycleState(task, a);
  const slug = a.slug || a.name || a.id || '';
  const branch = _workerBranchLabel(a);
  const actionLabel = _agentCardCurrentOrLastActionLabel(a);
  let html = '<div class="agent-card-body cell-body cell-body--worker">';
  html += _agentCardTooltipHtml(slug, 14, 'cell-name cell-worker-slug', 'data-worker-slug="' + esc(slug) + '"');
  if (taskId) {
    html += '<div class="agent-card-line cell-task cell-worker-task cell-worker-task--clickable"'
      + ' onclick="event.stopPropagation(); openTaskInBoard(\'' + esc(taskId) + '\');"'
      + ' title="' + esc(taskId) + ' — open in board">'
      + esc(taskId)
      + '</div>';
  } else {
    html += '<div class="agent-card-line cell-task cell-worker-task cell-worker-task--empty">no task</div>';
  }
  html += '<div class="agent-card-line cell-worker-cycle">'
    + esc('cycle: ' + cycle)
    + '</div>';
  const diffLabel = _workerDiffLabel(a);
  if (diffLabel) {
    html += '<div class="agent-card-line cell-worker-diff">' + esc(diffLabel) + '</div>';
  }
  html += '<div class="agent-card-line cell-worker-branch">'
    + _agentCardTooltipHtml(branch, 18, 'cell-worker-branch-name', 'data-worktree-branch="' + esc(a.worktree_branch || a.current_branch || '') + '"')
    + '</div>';
  html += '<div class="agent-card-line cell-worker-activity" title="' + esc(actionLabel) + '">' + esc(actionLabel) + '</div>';
  html += '</div>';
  return html;
}

function _renderEngineerCardBody(a, askingText) {
  const workers = _workersForEngineer(a.id);
  const queueDepth = _engineerQueueDepth(a.id);
  const actionLabel = _agentCardCurrentOrLastActionLabel(a);
  const workerLabel = workers.length === 1 ? 'worker' : 'workers';
  const identity = _agentCardPrimaryClassIdentity(a);
  let html = '<div class="agent-card-body cell-body cell-body--engineer">';
  html += '<div class="agent-card-line cell-name"'
    + (identity ? (' title="' + esc('Agent Class: ' + identity.label + ' · base kind ' + (identity.baseKindLabel || 'Engineer')) + '"') : '')
    + '>' + esc(_agentCardPrimaryDisplayName(a)) + '</div>';
  html += '<div class="agent-card-line cell-engineer-workers">'
    + '<span class="agent-card-state-mix">' + _agentStatusMixDots(workers) + '</span>'
    + '<span class="agent-card-state-count">' + esc(String(workers.length) + ' ' + workerLabel) + '</span>'
    + '</div>';
  html += '<div class="agent-card-line cell-engineer-queue">'
    + esc('queue: ' + queueDepth)
    + '</div>';
  html += '<div class="agent-card-line cell-engineer-activity" title="' + esc(actionLabel) + '">'
    + esc(actionLabel)
    + '</div>';
  if (askingText) {
    html += '<div class="agent-card-line cell-engineer-ask" title="' + esc(askingText) + '">awaiting input</div>';
  }
  html += '</div>';
  return html;
}

function _renderArchitectCellBody(a) {
  const stats = _architectStatsForCard(a, null);
  const actionLabel = _agentCardCurrentOrLastActionLabel(a);
  const identity = _agentCardPrimaryClassIdentity(a);
  let html = '<div class="agent-card-body cell-body cell-body--architect">';
  html += '<div class="agent-card-line cell-name"'
    + (identity ? (' title="' + esc('Agent Class: ' + identity.label + ' · base kind ' + (identity.baseKindLabel || 'Architect')) + '"') : '')
    + '>' + esc(_agentCardPrimaryDisplayName(a)) + '</div>';
  html += '<div class="agent-card-line cell-architect-stats">'
    + esc(stats.engineerCount + ' engineers')
    + '</div>';
  html += '<div class="agent-card-line cell-architect-activity" title="' + esc(actionLabel) + '">' + esc(actionLabel) + '</div>';
  html += '</div>';
  return html;
}

function _renderGenericAgentCardBody(a) {
  const subtitle = _agentCellSubtitle(a);
  const identity = _agentCardPrimaryClassIdentity(a);
  let html = '<div class="agent-card-body cell-body cell-body--generic">';
  html += '<div class="agent-card-line cell-name"'
    + (identity ? (' title="' + esc('Agent Class: ' + identity.label + ' · base kind ' + (identity.baseKindLabel || 'Agent')) + '"') : '')
    + '>' + esc(_agentCardPrimaryDisplayName(a)) + '</div>';
  if (subtitle) {
    html += '<div class="agent-card-line cell-task" title="' + esc(subtitle) + '">' + formatCode(subtitle) + '</div>';
  } else {
    html += '<div class="agent-card-line cell-task cell-task-empty">&nbsp;</div>';
  }
  const actionLabel = _agentCardCurrentOrLastActionLabel(a);
  html += '<div class="agent-card-line cell-generic-activity" title="' + esc(actionLabel) + '">' + esc(actionLabel) + '</div>';
  html += '</div>';
  return html;
}

function renderAgentCell(a, options) {
  options = options || {};
  const active = a.session_id && a.session_id === state.active_session_id;
  const selected = a.id === selectedAgentId;
  const childCount = (state.children[a.id] || []).length;
  const doneFlourish = _getAgentDoneFlourish(a.id);
  const cls = ['cell'];
  if (active) cls.push('active');
  if (selected) cls.push('selected');
  if (a.id === focusedItemId) cls.push('focused');
  if (a.status === 'stopped') cls.push('stopped');
  const _isArchitect = (a.kind || '') === 'architect';
  const _isEngineerKind = (a.kind || '') === 'engineer';
  const _isWorker = (a.kind || '') === 'worker';
  const _isDismissed = _isLifecycleDismissedAgent(a);
  // Check if this agent is the engineer for its group
  const _gs = (state.group_settings || {})[a.group];
  const _isDesignatedEngineer = _gs && _gs.engineer_agent_id === a.id;
  // Check if engineer is awaiting human input
  const _engineerWs = _isDesignatedEngineer && state.engineer_settings
    ? state.engineer_settings[a.group] : null;
  const _engineerPaused = !!(_engineerWs && _engineerWs.paused);
  const _engineerAsking = _engineerWs && _engineerWs.pending_question;
  const _isDigestRecipient = _isEngineerKind || _isArchitect;
  const _cardDigestSettings = state.agent_digest_settings
    ? state.agent_digest_settings[String(a.id || '')] : null;
  let _digestPaused = !!(_cardDigestSettings && _cardDigestSettings.paused);
  if (!_cardDigestSettings && _isDigestRecipient && _isDesignatedEngineer) _digestPaused = _engineerPaused;
  const _isRetainedExecutionOwner = !!(options.retainedExecutionOwner && _isArchitect && !selected);
  if (_isArchitect) cls.push('architect');
  if (_isEngineerKind) cls.push('engineer');
  if (_isWorker) cls.push('worker');
  if (_isDismissed) cls.push('dismissed');
  if (_isRetainedExecutionOwner) cls.push('retained-execution-owner');

  const statusCls = _isDismissed ? 'dismissed' : agentStatusClass(a);
  const titleParts = [a.name, `(${a.status})`];
  if (_isDismissed) titleParts.push('\u2014 dismissed');
  if (a.needs_attention && a.error_message) titleParts.push(`\u2014 ${a.error_message}`);
  else if (a.activity_detail) titleParts.push(`\u2014 ${a.activity_detail}`);
  const statusClasses = ['cell-status', statusCls];
  const statusAttrs = [];
  const showDoneFlourish = !!(doneFlourish && statusCls !== 'attention');
  if (showDoneFlourish) {
    statusClasses.push('cell-status-done-flourish');
    statusAttrs.push(
      `style="--cell-status-done-duration:${doneFlourish.duration_ms}ms;--cell-status-done-delay:-${doneFlourish.elapsed_ms}ms"`
    );
  }
  if (statusCls === 'attention') {
    statusAttrs.push(`title="${esc(a.error_message || 'Needs attention')}"`);
  }

  let h = `<div class="${cls.join(' ')}" draggable="true" data-drag-id="${a.id}" data-drag-type="agent" data-drag-group="${esc(a.group)}" data-nav-id="${esc(a.id)}"`;
  if (_isRetainedExecutionOwner) h += ' data-retained-execution-owner="true"';
  if (_isDismissed) h += ` data-dismissed-at="${esc(_agentDismissedAt(a))}"`;
  h += ` onclick="onAgentClick('${a.id}')" ondblclick="onAgentDblClick('${a.id}')" oncontextmenu="onCellContextMenu(event,'${a.id}')" onauxclick="if(event.button===1){event.preventDefault();removeAgent('${a.id}')}" title="${esc(titleParts.join(' '))}">`;
  h += `<div class="${statusClasses.join(' ')}"${statusAttrs.length ? ' ' + statusAttrs.join(' ') : ''}>`;
  if (_isDismissed) h += '\u2013';
  else if (statusCls === 'attention') h += '!';
  else if (showDoneFlourish) h += `<span class="cell-status-flourish-label">${esc(doneFlourish.label)}</span>`;
  h += `</div>`;
  if (_isDesignatedEngineer && !_isDigestRecipient && !_isWorker) {
    const engineerGroupArg = encodeURIComponent(a.group || '');
    h += _renderAgentCardControls(a, {
      dismissed: _isDismissed,
      paused: _engineerPaused,
      pauseTitle: _engineerPaused ? 'Resume Engineer event delivery' : 'Pause Engineer event delivery',
      pauseOnclick: `event.stopPropagation();engineerTogglePauseForGroup(decodeURIComponent('${engineerGroupArg}'))`,
    });
  } else {
    const digestAgentArg = encodeURIComponent(a.id || '');
    h += _renderAgentCardControls(a, {
      dismissed: _isDismissed,
      paused: _digestPaused,
      pauseTitle: _digestPaused ? 'Resume event delivery' : 'Pause event delivery',
      pauseOnclick: `event.stopPropagation();toggleDigestPauseForAgent(decodeURIComponent('${digestAgentArg}'))`,
    });
  }
  if (_isWorker) h += _renderWorkerCardBody(a);
  else if (_isEngineerKind || _isDesignatedEngineer) h += _renderEngineerCardBody(a, _engineerAsking ? _engineerWs.pending_question : '');
  else if (_isArchitect) h += _renderArchitectCellBody(a);
  else h += _renderGenericAgentCardBody(a);
  h += _renderAgentProviderBadge(a.agent_type, 'cell-provider');
  h += _renderAgentContextMeter(a);
  const badge = _agentCardKindBadge(a, _isDismissed);
  h += '<div class="agent-card-kind ui-badge ui-badge--micro ' + esc(badge.cls) + '"'
    + (badge.title ? ' title="' + esc(badge.title) + '"' : '')
    + '>' + esc(badge.label) + '</div>';
  if (childCount > 0) {
    h += `<div class="cell-term-count">${childCount}</div>`;
  }
  if (_isDismissed) {
    const rehireAction = _isArchitect ? 'rehireArchitect' : 'rehireEngineer';
    const rehireTitle = _isArchitect ? 'Rehire architect' : 'Rehire engineer';
    h += `<button class="cell-relaunch cell-rehire" onclick="event.stopPropagation();${rehireAction}('${a.id}')" title="${rehireTitle}">\u21BB rehire</button>`;
  } else if (a.status === 'stopped') {
    h += `<button class="cell-relaunch" onclick="event.stopPropagation();relaunchAgent('${a.id}')" title="Relaunch">\u21BB relaunch</button>`;
  }
  h += `</div>`;
  return h;
}

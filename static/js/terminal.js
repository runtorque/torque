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
// Semantic, per-composer draft history. It intentionally remains in-memory like
// drafts: a sent message or daemon restart can never be resurrected with undo.
let _terminalComposeHistory = Object.create(null);
let _terminalComposeReleasedPreviewUrls = Object.create(null);
let _terminalComposeHeights = Object.create(null);
// Terminal-flicker Phase 1: per-cell memo of the inputs the last autoresize
// ran with (text content + stored user height). Every delta-driven render pass
// re-runs `_terminalComposeAutoResize`, which forced a `taskAutoResize` reflow
// + `_terminalComposeApplyHeight` style write on the focused composer on every
// pass — the differing scrollHeight resized the stage, tripped the xterm
// ResizeObserver, and flashed the terminal. We skip those style writes when the
// focused composer's height inputs are unchanged. See `_terminalComposeAutoResize`.
let _terminalComposeAutoResizeMemo = Object.create(null);
let _terminalComposeResizeDrag = null;
let _terminalComposeSelectedAttachmentByCell = Object.create(null);
let _terminalComposePreviewOverlay = null;
let _terminalComposePreviewKeyHandler = null;
let _terminalComposeHistoryOpenCellId = '';
let _terminalComposeTaskDropdownCellId = '';
let _terminalComposeTaskDropdownIdx = -1;
let _terminalComposeTaskDropdownResults = [];
let _terminalComposeSlashDropdownCellId = '';
let _terminalComposeSlashDropdownIdx = -1;
let _terminalComposeSlashDropdownResults = [];
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
// The latest submitted DM per target; Esc uses this exact session/key only.
let _terminalDirectMessageActiveTurnByAgent = Object.create(null);
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

var TERMINAL_COMPOSE_HISTORY_MAX_ENTRIES = 64;
var TERMINAL_COMPOSE_HISTORY_MAX_BYTES = 256 * 1024;
var TERMINAL_COMPOSE_MIN_HEIGHT = 68;
var TERMINAL_COMPOSE_DEFAULT_MAX_HEIGHT = 96;
var TERMINAL_COMPOSE_MAX_HEIGHT_FALLBACK = 260;
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
// Wheel/trackpad scroll-up can arrive while xterm is actively appending output.
// Detach only after a small intentional movement (roughly two terminal rows) so
// incidental near-bottom headroom keeps useful tailing, but a real scroll-up
// disables follow-tail before the next output callback can pull the viewport
// back down.
var EMBEDDED_TERMINAL_SCROLL_UP_DETACH_THRESHOLD_PX = 24;
var EMBEDDED_TERMINAL_WHEEL_INTENT_RESET_MS = 350;
var EMBEDDED_TERMINAL_USER_SCROLL_INTENT_MS = 900;

function isEmbeddedTerminalMode() {
  return !!(state && state.runtime && state.runtime.embedded_terminal);
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

function _embeddedTerminalActiveElementAllowsFocus(force, active, expectedActive) {
  // A queued first-mount focus must not become an asynchronous focus command
  // after the operator has already moved on to another control.  In
  // particular, a terminal socket open/snapshot can land between a grid
  // activity render and the next animation frame.
  if (!force && expectedActive && active !== expectedActive) return false;
  if (document.querySelector && document.querySelector('.overlay.visible')) return false;
  if (!active || active === document.body) return true;
  const workspace = document.getElementById('terminal-workspace');
  if (workspace && typeof workspace.contains === 'function' && workspace.contains(active)) {
    if (!force && _terminalWorkspaceFocusBelongsToComposerOrDirectMessage(active)) return false;
    return true;
  }
  if (force) return true;
  const tag = (active.tagName || '').toUpperCase();
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || active.isContentEditable) {
    return false;
  }
  return true;
}

function _embeddedTerminalCanTakeFocus(force) {
  if (!_embeddedTerminal || !isEmbeddedTerminalMode()) return false;
  if (!_embeddedTerminalSessionKey) return false;
  if (!force && _embeddedTerminalPendingFocusKey !== _embeddedTerminalSessionKey) {
    return false;
  }
  return _embeddedTerminalActiveElementAllowsFocus(!!force, document.activeElement, null);
}

function _terminalWorkspaceFocusBelongsToComposerOrDirectMessage(active) {
  if (!active) return false;
  if (active.classList && active.classList.contains('terminal-compose-input')) return true;
  if (typeof active.closest !== 'function') return false;
  return !!(
    active.closest('.terminal-compose')
    || active.closest('.terminal-direct-messages')
  );
}

function focusEmbeddedTerminalWorkspace(force) {
  const explicit = !!force;
  // Consume a non-forced request before scheduling it. Socket open and
  // snapshot events often arrive together; they are one first-mount intent,
  // not permission to reclaim focus on later agent-activity renders.
  if (!_embeddedTerminal || !isEmbeddedTerminalMode() || !_embeddedTerminalSessionKey) return false;
  if (!explicit && _embeddedTerminalPendingFocusKey !== _embeddedTerminalSessionKey) return false;
  const expectedActive = document.activeElement || null;
  if (!explicit) _embeddedTerminalPendingFocusKey = '';
  // A missing active element is not evidence that the desktop focus is free:
  // WKWebView can report null while another document/native control owns the
  // keyboard. Keep explicit focus available, but do not turn that unknown
  // state into an asynchronous first-mount focus licence.
  if (!explicit && !expectedActive) return false;
  if (!_embeddedTerminalActiveElementAllowsFocus(explicit, expectedActive, null)) return false;
  const expectedKey = _embeddedTerminalSessionKey;
  requestAnimationFrame(function() {
    if (_embeddedTerminalSessionKey !== expectedKey) return;
    if (!_embeddedTerminalActiveElementAllowsFocus(explicit, document.activeElement, expectedActive)) return;
    if (typeof _embeddedTerminal.focus === 'function') _embeddedTerminal.focus();
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

function renderTerminalWorkspace(opts) {
  opts = opts || {};
  const root = document.getElementById('terminal-workspace');
  if (!root) return;
  _terminalComposePersistFromDom(root);
  // A detached panel window is a separate full webview showing only its panel;
  // the terminal workspace is CSS-hidden there. It must never open a PTY socket,
  // fit() a zero-size xterm, or send resize/focus frames — doing so clobbers the
  // shared session down to the 20-col floor in the main window. Behave like the
  // non-embedded branch: tear down any embedded terminal and bail.
  if (typeof _detachedWindowActive === 'function' && _detachedWindowActive()) {
    root.innerHTML = '';
    root.classList.remove('active');
    _disposeEmbeddedTerminal();
    return;
  }
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
  if (opts.suppressTerminalFocus && workspaceState && workspaceState.terminal
      && workspaceState.terminal.focus
      && _terminalDirectMessageFocusIsTerminal(root)) {
    workspaceState.terminal.focus = null;
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
      + '<div class="terminal-empty ui-state ui-state--empty ui-state--fill">'
      + '  <div class="terminal-empty-title ui-state__title">Select an agent</div>'
      + '  <div class="terminal-empty-body ui-state__message">Choose an agent, worker, engineer, architect, or legacy terminal from the grid to view its session here.</div>'
      + '  <div class="terminal-empty-meta ui-state__meta">Manual terminal creation has moved out of the operator UI.</div>'
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
        + '  <div class="terminal-empty-title ui-state__title">' + esc(cell.name) + ' is stopped</div>'
        + '  <div class="terminal-empty-body ui-state__message">This legacy terminal remains available in the grid so you can inspect or delete it, but manual terminal relaunch is no longer available from the UI.</div>'
        + '  <div class="terminal-empty-meta ui-state__meta">Select an agent card to switch the workspace to an active session.</div>'
      : ''
        + '  <div class="terminal-empty-title ui-state__title">' + esc(cell.name) + ' is stopped</div>'
        + '  <div class="terminal-empty-body ui-state__message">Relaunch this session to put it back in the workspace and return keyboard focus to the shell.</div>'
        + '  <div class="ui-state__actions"><button class="terminal-empty-btn btn-secondary" onclick="relaunchAgent(\'' + esc(cell.id) + '\')">Relaunch</button></div>'
        + '  <div class="terminal-empty-meta ui-state__meta">When it comes back, Torque will focus the terminal automatically.</div>';
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

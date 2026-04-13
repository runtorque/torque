/* Embedded terminal workspace for standalone PTY mode */
let _embeddedTerminal = null;
let _embeddedTerminalFit = null;
let _embeddedTerminalWs = null;
let _embeddedTerminalSessionKey = '';
let _embeddedTerminalResizeObserver = null;
let _embeddedTerminalDataHandler = null;
let _embeddedTerminalPendingFocusKey = '';

function isEmbeddedTerminalMode() {
  return !!(state && state.runtime && state.runtime.embedded_terminal);
}

function _terminalCurrentGroupName() {
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

function _terminalGroupCells(group) {
  const out = [];
  if (!group || !state || !state.groups || !state.agents) return out;
  const ids = state.groups[group] || [];
  for (let i = 0; i < ids.length; i++) {
    const cell = state.agents[ids[i]];
    if (!cell) continue;
    out.push(cell);
    if (cell.cell_type === 'agent') {
      const kids = state.children && state.children[cell.id] ? state.children[cell.id] : [];
      for (let j = 0; j < kids.length; j++) {
        const child = state.agents[kids[j]];
        if (child) out.push(child);
      }
    }
  }
  return out;
}

function _resolveTerminalWorkspaceCell() {
  if (!state || !state.agents) return null;
  if (selectedTerminalId && state.agents[selectedTerminalId]) {
    return state.agents[selectedTerminalId];
  }
  if (state.active_session_id) {
    for (const id in state.agents) {
      const cell = state.agents[id];
      if (cell.session_id === state.active_session_id) {
        selectedTerminalId = id;
        return cell;
      }
    }
  }
  if (selectedAgentId && state.agents[selectedAgentId]) {
    selectedTerminalId = selectedAgentId;
    return state.agents[selectedAgentId];
  }
  const group = _terminalCurrentGroupName();
  const cells = _terminalGroupCells(group);
  if (cells.length) {
    selectedTerminalId = cells[0].id;
    return cells[0];
  }
  const ids = Object.keys(state.agents);
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
  if (!groupLabel) return null;
  return {
    label: 'New Terminal',
    onclick: 'quickAddTerminal(\''
      + esc(groupLabel)
      + '\',\''
      + esc(agentTarget && agentTarget.id ? agentTarget.id : '')
      + '\')',
  };
}

function _embeddedTerminalCanTakeFocus(force) {
  if (!_embeddedTerminal || !isEmbeddedTerminalMode()) return false;
  if (!_embeddedTerminalSessionKey) return false;
  if (!force && _embeddedTerminalPendingFocusKey !== _embeddedTerminalSessionKey) {
    return false;
  }
  if (document.querySelector && document.querySelector('.overlay.visible')) return false;
  const active = document.activeElement;
  if (!active || active === document.body) return true;
  const workspace = document.getElementById('terminal-workspace');
  if (workspace && typeof workspace.contains === 'function' && workspace.contains(active)) {
    return true;
  }
  if (force) return true;
  const tag = (active.tagName || '').toUpperCase();
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || active.isContentEditable) {
    return false;
  }
  return true;
}

function focusEmbeddedTerminalWorkspace(force) {
  if (!_embeddedTerminalCanTakeFocus(!!force)) return false;
  const expectedKey = _embeddedTerminalSessionKey;
  requestAnimationFrame(function() {
    if (_embeddedTerminalSessionKey !== expectedKey) return;
    if (!_embeddedTerminalCanTakeFocus(!!force)) return;
    if (typeof _embeddedTerminal.focus === 'function') _embeddedTerminal.focus();
    _embeddedTerminalPendingFocusKey = '';
  });
  return true;
}

function _ensureTerminalWorkspaceDom(root) {
  let shell = root.querySelector('.terminal-shell');
  if (!shell) {
    root.innerHTML = ''
      + '<div class="terminal-shell">'
      + '  <div class="terminal-topbar"></div>'
      + '  <div class="terminal-tabs"></div>'
      + '  <div class="terminal-stage"></div>'
      + '  <div class="terminal-statusbar"></div>'
      + '</div>';
    shell = root.querySelector('.terminal-shell');
  }
  return {
    shell: shell,
    topbar: shell.querySelector('.terminal-topbar'),
    tabs: shell.querySelector('.terminal-tabs'),
    stage: shell.querySelector('.terminal-stage'),
    statusbar: shell.querySelector('.terminal-statusbar'),
  };
}

function _renderTerminalTabs(cells, activeId) {
  if (!cells.length) return '<div class="terminal-tabs-empty">No sessions</div>';
  let html = '';
  for (let i = 0; i < cells.length; i++) {
    const cell = cells[i];
    const active = cell.id === activeId;
    const stopped = cell.status === 'stopped' || !cell.session_id;
    html += '<button class="terminal-tab'
      + (active ? ' active' : '')
      + (stopped ? ' stopped' : '')
      + '" onclick="focusAgent(\'' + esc(cell.id) + '\')">'
      + '<span class="terminal-tab-dot ' + esc(agentStatusClass(cell)) + '"></span>'
      + '<span class="terminal-tab-label">' + esc(cell.name) + '</span>'
      + (cell.cell_type === 'terminal' ? '<span class="terminal-tab-kind">term</span>' : '')
      + '</button>';
  }
  return html;
}

function _disposeEmbeddedTerminal() {
  if (_embeddedTerminalResizeObserver) {
    _embeddedTerminalResizeObserver.disconnect();
    _embeddedTerminalResizeObserver = null;
  }
  if (_embeddedTerminalWs) {
    _embeddedTerminalWs.onopen = null;
    _embeddedTerminalWs.onmessage = null;
    _embeddedTerminalWs.onerror = null;
    _embeddedTerminalWs.onclose = null;
    _embeddedTerminalWs.close();
    _embeddedTerminalWs = null;
  }
  if (_embeddedTerminal) {
    if (_embeddedTerminalDataHandler && typeof _embeddedTerminalDataHandler.dispose === 'function') {
      _embeddedTerminalDataHandler.dispose();
    }
    _embeddedTerminal.dispose();
    _embeddedTerminal = null;
    _embeddedTerminalFit = null;
    _embeddedTerminalDataHandler = null;
  }
  _embeddedTerminalSessionKey = '';
  _embeddedTerminalPendingFocusKey = '';
}

function _embeddedTerminalUrl(cell) {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return protocol + '//' + location.host + '/ws/terminal/' + encodeURIComponent(cell.id);
}

function _scheduleEmbeddedTerminalFit() {
  if (!_embeddedTerminal || !_embeddedTerminalFit) return;
  requestAnimationFrame(function() {
    if (!_embeddedTerminal || !_embeddedTerminalFit) return;
    _embeddedTerminalFit.fit();
    if (_embeddedTerminalWs && _embeddedTerminalWs.readyState === WebSocket.OPEN) {
      _embeddedTerminalWs.send(JSON.stringify({
        type: 'resize',
        cols: _embeddedTerminal.cols,
        rows: _embeddedTerminal.rows,
      }));
      _embeddedTerminalWs.send(JSON.stringify({ type: 'focus' }));
    }
  });
}

function _connectEmbeddedTerminal(cell, surface) {
  _disposeEmbeddedTerminal();
  var expectedSessionId = cell.session_id || '';
  var sessionKey = cell.id + ':' + expectedSessionId;
  _embeddedTerminalSessionKey = sessionKey;
  _embeddedTerminalPendingFocusKey = sessionKey;
  _embeddedTerminal = new Terminal({
    allowProposedApi: true,
    allowTransparency: false,
    convertEol: false,
    cols: 120,
    rows: 32,
    cursorBlink: true,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    fontSize: 13,
    lineHeight: 1.0,
    letterSpacing: 0,
    scrollback: 5000,
    theme: {
      background: '#0d1117',
      foreground: '#e6edf3',
      cursor: '#58a6ff',
      selectionBackground: 'rgba(88,166,255,0.28)',
    },
  });
  _embeddedTerminalFit = new FitAddon.FitAddon();
  _embeddedTerminal.loadAddon(_embeddedTerminalFit);
  _embeddedTerminal.open(surface);
  _embeddedTerminalDataHandler = _embeddedTerminal.onData(function(data) {
    if (_embeddedTerminalWs && _embeddedTerminalWs.readyState === WebSocket.OPEN) {
      _embeddedTerminalWs.send(JSON.stringify({ type: 'input', data: data }));
    }
  });
  _embeddedTerminalResizeObserver = new ResizeObserver(function() {
    _scheduleEmbeddedTerminalFit();
  });
  _embeddedTerminalResizeObserver.observe(surface);
  _embeddedTerminalWs = new WebSocket(_embeddedTerminalUrl(cell));
  var socket = _embeddedTerminalWs;
  function isCurrentSessionMessage(msg) {
    if (_embeddedTerminalSessionKey !== sessionKey) return false;
    if (_embeddedTerminalWs !== socket) return false;
    if (msg && typeof msg.session_id === 'string' && msg.session_id !== expectedSessionId) {
      return false;
    }
    return true;
  }
  _embeddedTerminalWs.onopen = function() {
    if (!isCurrentSessionMessage()) return;
    _scheduleEmbeddedTerminalFit();
    focusEmbeddedTerminalWorkspace(false);
  };
  _embeddedTerminalWs.onmessage = function(event) {
    const msg = JSON.parse(event.data);
    if (!isCurrentSessionMessage(msg)) return;
    if (msg.type === 'snapshot') {
      _embeddedTerminal.reset();
      if (msg.data) _embeddedTerminal.write(msg.data);
      _scheduleEmbeddedTerminalFit();
      focusEmbeddedTerminalWorkspace(false);
    } else if (msg.type === 'output' && msg.data) {
      _embeddedTerminal.write(msg.data);
    }
  };
  _embeddedTerminalWs.onclose = function() {
    if (isCurrentSessionMessage()) {
      const status = document.querySelector('#terminal-workspace .terminal-statusbar');
      if (status) status.setAttribute('data-closed', '1');
    }
  };
  _scheduleEmbeddedTerminalFit();
}

function renderTerminalWorkspace() {
  const root = document.getElementById('terminal-workspace');
  if (!root) return;
  if (!isEmbeddedTerminalMode()) {
    root.innerHTML = '';
    root.classList.remove('active');
    _disposeEmbeddedTerminal();
    return;
  }
  root.classList.add('active');
  const group = _terminalCurrentGroupName();
  const groupLabel = group || '';
  const cells = _terminalGroupCells(group);
  const cell = _resolveTerminalWorkspaceCell();
  const agentTarget = _terminalTargetAgent(cell);
  const primaryAction = _terminalPrimaryAction(groupLabel, agentTarget);
  const topbarAction = cell && cell.session_id ? primaryAction : null;
  const showTabs = cells.length > 1;
  const displayPath = _terminalDisplayPath(cell);
  const dom = _ensureTerminalWorkspaceDom(root);
  const title = cell && cell.name ? cell.name : 'Terminal';
  dom.topbar.innerHTML = ''
    + '<div class="terminal-topbar-left">'
    + '  <span class="terminal-title">' + esc(title) + '</span>'
    + '  <span class="terminal-group-pill">' + esc(groupLabel || 'Standalone') + '</span>'
    + '</div>'
    + '<div class="terminal-topbar-right">'
    + (topbarAction
      ? '  <button class="terminal-topbar-btn terminal-topbar-btn-primary" onclick="' + topbarAction.onclick + '">' + topbarAction.label + '</button>'
      : '')
    + '</div>';
  dom.tabs.classList.toggle('terminal-tabs-hidden', !showTabs);
  dom.tabs.innerHTML = showTabs ? _renderTerminalTabs(cells, cell ? cell.id : '') : '';

  if (!cell) {
    dom.stage.innerHTML = ''
      + '<div class="terminal-empty">'
      + '  <div class="terminal-empty-title">Open a shell</div>'
      + '  <div class="terminal-empty-body">Start a standalone terminal for this workspace and Loom will drop you into it ready to type.</div>'
      + (primaryAction
        ? '  <button class="terminal-empty-btn" onclick="' + primaryAction.onclick + '">' + primaryAction.label + '</button>'
        : '')
      + '  <div class="terminal-empty-meta">The terminal will take focus automatically when it opens.</div>'
      + '</div>';
    dom.statusbar.textContent = 'Standalone PTY workspace';
    _disposeEmbeddedTerminal();
    return;
  }

  const sessionKey = cell.id + ':' + (cell.session_id || '');
  if (!cell.session_id) {
    dom.stage.innerHTML = ''
      + '<div class="terminal-empty">'
      + '  <div class="terminal-empty-title">' + esc(cell.name) + ' is stopped</div>'
      + '  <div class="terminal-empty-body">Relaunch this session to put it back in the workspace and return keyboard focus to the shell.</div>'
      + '  <button class="terminal-empty-btn" onclick="relaunchAgent(\'' + esc(cell.id) + '\')">Relaunch</button>'
      + '  <div class="terminal-empty-meta">When it comes back, Loom will focus the terminal automatically.</div>'
      + '</div>';
    dom.statusbar.textContent = _terminalStatusLabel(cell);
    _disposeEmbeddedTerminal();
    return;
  }

  if (_embeddedTerminalSessionKey !== sessionKey || !dom.stage.querySelector('.terminal-surface')) {
    dom.stage.innerHTML = '<div class="terminal-surface"></div>';
    _connectEmbeddedTerminal(cell, dom.stage.querySelector('.terminal-surface'));
  }

  dom.statusbar.textContent = (displayPath || 'No directory') + '  |  ' + _terminalStatusLabel(cell);
  dom.statusbar.title = cell.current_path || cell.directory || '';
}

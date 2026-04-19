/* Embedded terminal workspace for standalone PTY mode */
let _embeddedTerminal = null;
let _embeddedTerminalFit = null;
let _embeddedTerminalWs = null;
let _embeddedTerminalSessionKey = '';
let _embeddedTerminalResizeObserver = null;
let _embeddedTerminalDataHandler = null;
let _embeddedTerminalPendingFocusKey = '';
let _embeddedTerminalDropSurface = null;
let _embeddedTerminalDropHandlers = null;
let _embeddedTerminalDropDepth = 0;

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
    if (!cell) continue;
    seen[id] = true;
    out.push(cell);
    if (cell.cell_type === 'agent') {
      const kids = state.children && state.children[cell.id] ? state.children[cell.id] : [];
      for (let j = 0; j < kids.length; j++) {
        const childId = kids[j];
        if (seen[childId]) continue;
        const child = state.agents[childId];
        if (!child) continue;
        seen[childId] = true;
        out.push(child);
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
  if (_embeddedTerminalDropSurface && _embeddedTerminalDropHandlers) {
    if (typeof _embeddedTerminalDropSurface.removeEventListener === 'function') {
      _embeddedTerminalDropSurface.removeEventListener('dragenter', _embeddedTerminalDropHandlers.dragenter, true);
      _embeddedTerminalDropSurface.removeEventListener('dragover', _embeddedTerminalDropHandlers.dragover, true);
      _embeddedTerminalDropSurface.removeEventListener('dragleave', _embeddedTerminalDropHandlers.dragleave, true);
      _embeddedTerminalDropSurface.removeEventListener('drop', _embeddedTerminalDropHandlers.drop, true);
    }
    _setEmbeddedTerminalDropTarget(_embeddedTerminalDropSurface, false);
  }
  _embeddedTerminalDropSurface = null;
  _embeddedTerminalDropHandlers = null;
  _embeddedTerminalDropDepth = 0;
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

function _embeddedTerminalDroppedFiles(dataTransfer) {
  if (!dataTransfer || !dataTransfer.files || !dataTransfer.files.length) return [];
  return Array.prototype.slice.call(dataTransfer.files);
}

function _embeddedTerminalHasDraggedFiles(dataTransfer) {
  if (!dataTransfer) return false;
  if (dataTransfer.types && typeof dataTransfer.types.length === 'number') {
    if (Array.prototype.indexOf.call(dataTransfer.types, 'Files') !== -1) return true;
  }
  if (dataTransfer.items && typeof dataTransfer.items.length === 'number') {
    for (var i = 0; i < dataTransfer.items.length; i++) {
      var item = dataTransfer.items[i];
      if (item && item.kind === 'file') return true;
    }
  }
  return _embeddedTerminalDroppedFiles(dataTransfer).length > 0;
}

function _embeddedTerminalDroppedImages(dataTransfer) {
  var files = _embeddedTerminalDroppedFiles(dataTransfer);
  return files.filter(function(file) {
    return !!(file && file.type && file.type.indexOf('image/') === 0);
  });
}

function _embeddedTerminalDropUploadId(cell) {
  var raw = 'terminal-drop-' + (cell && cell.id ? cell.id : 'session') + '-' + (cell && cell.session_id ? cell.session_id : 'session');
  raw = raw.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
  return raw || 'terminal-drop';
}

function _shellQuoteTerminalPath(path) {
  return "'" + String(path || '').replace(/'/g, "'\"'\"'") + "'";
}

function _setEmbeddedTerminalDropTarget(surface, active) {
  if (!surface || !surface.classList) return;
  surface.classList.toggle('terminal-drop-target', !!active);
  var status = document.querySelector('#terminal-workspace .terminal-statusbar');
  if (status && status.classList) status.classList.toggle('terminal-drop-target', !!active);
}

async function _uploadEmbeddedTerminalImages(cell, files) {
  var taskId = _embeddedTerminalDropUploadId(cell);
  var uploaded = await Promise.all(files.map(async function(file) {
    var fd = new FormData();
    fd.append('task_id', taskId);
    fd.append('file', file);
    try {
      var r = await fetch('/api/upload', { method: 'POST', body: fd });
      var res = await r.json();
      if (!res || !res.ok || !Array.isArray(res.data)) return [];
      return res.data
        .map(function(entry) { return entry && entry.path ? entry.path : ''; })
        .filter(Boolean);
    } catch (e) {
      return [];
    }
  }));
  return uploaded.reduce(function(paths, batch) {
    return paths.concat(batch);
  }, []);
}

function _attachEmbeddedTerminalDropHandlers(cell, surface) {
  if (!surface || typeof surface.addEventListener !== 'function') return;
  _embeddedTerminalDropSurface = surface;
  _embeddedTerminalDropDepth = 0;
  _embeddedTerminalDropHandlers = {
    dragenter: function(e) {
      if (!_embeddedTerminalHasDraggedFiles(e && e.dataTransfer)) return;
      _embeddedTerminalDropDepth += 1;
      _setEmbeddedTerminalDropTarget(surface, true);
    },
    dragover: function(e) {
      if (!_embeddedTerminalHasDraggedFiles(e && e.dataTransfer)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
      _setEmbeddedTerminalDropTarget(surface, true);
    },
    dragleave: function(e) {
      if (!_embeddedTerminalHasDraggedFiles(e && e.dataTransfer)) return;
      _embeddedTerminalDropDepth = Math.max(0, _embeddedTerminalDropDepth - 1);
      if (_embeddedTerminalDropDepth > 0) return;
      if (e.relatedTarget && typeof surface.contains === 'function' && surface.contains(e.relatedTarget)) {
        return;
      }
      _setEmbeddedTerminalDropTarget(surface, false);
    },
    drop: async function(e) {
      var files = _embeddedTerminalDroppedFiles(e && e.dataTransfer);
      if (!files.length) return;
      e.preventDefault();
      _embeddedTerminalDropDepth = 0;
      _setEmbeddedTerminalDropTarget(surface, false);
      var sessionKey = _embeddedTerminalSessionKey;
      var images = _embeddedTerminalDroppedImages(e.dataTransfer);
      if (!images.length) {
        if (_embeddedTerminalSessionKey === sessionKey) {
          _embeddedTerminalPendingFocusKey = _embeddedTerminalSessionKey;
          focusEmbeddedTerminalWorkspace(false);
        }
        return;
      }
      var paths = await _uploadEmbeddedTerminalImages(cell, images);
      if (_embeddedTerminalSessionKey !== sessionKey) return;
      if (paths.length && _embeddedTerminalWs && _embeddedTerminalWs.readyState === WebSocket.OPEN) {
        _embeddedTerminalWs.send(JSON.stringify({
          type: 'input',
          data: paths.map(_shellQuoteTerminalPath).join(' ') + ' ',
        }));
      }
      _embeddedTerminalPendingFocusKey = _embeddedTerminalSessionKey;
      focusEmbeddedTerminalWorkspace(false);
    },
  };
  // Capture-phase listeners ensure the workspace still sees file drags
  // before xterm's helper textarea can swallow them.
  surface.addEventListener('dragenter', _embeddedTerminalDropHandlers.dragenter, true);
  surface.addEventListener('dragover', _embeddedTerminalDropHandlers.dragover, true);
  surface.addEventListener('dragleave', _embeddedTerminalDropHandlers.dragleave, true);
  surface.addEventListener('drop', _embeddedTerminalDropHandlers.drop, true);
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

// Max attempts to re-open the terminal WS after a drop (e.g. daemon
// restart). Each attempt sleeps _EMBEDDED_TERMINAL_RETRY_MS between
// tries, giving ~15s of cover for a standalone-mode restart.
var _EMBEDDED_TERMINAL_RETRY_MS = 1000;
var _EMBEDDED_TERMINAL_MAX_RETRIES = 15;

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
  _attachEmbeddedTerminalDropHandlers(cell, surface);
  try { _embeddedTerminalFit.fit(); } catch (e) { /* container not measurable yet */ }
  // Shift+Enter → send ESC+CR so TUIs like Claude Code treat it as a
  // soft newline instead of submitting. xterm.js default maps Shift+Enter
  // to plain `\r` (same as Enter), which submits prematurely.
  if (typeof _embeddedTerminal.attachCustomKeyEventHandler === 'function') {
    _embeddedTerminal.attachCustomKeyEventHandler(function(e) {
      if (e.type !== 'keydown') return true;
      if (e.key === 'Enter' && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (_embeddedTerminalWs && _embeddedTerminalWs.readyState === WebSocket.OPEN) {
          _embeddedTerminalWs.send(JSON.stringify({ type: 'input', data: '\x1b\r' }));
        }
        return false;
      }
      return true;
    });
  }
  _embeddedTerminalDataHandler = _embeddedTerminal.onData(function(data) {
    if (_embeddedTerminalWs && _embeddedTerminalWs.readyState === WebSocket.OPEN) {
      _embeddedTerminalWs.send(JSON.stringify({ type: 'input', data: data }));
    }
  });
  _embeddedTerminalResizeObserver = new ResizeObserver(function() {
    _scheduleEmbeddedTerminalFit();
  });
  _embeddedTerminalResizeObserver.observe(surface);
  _openEmbeddedTerminalSocket(cell, sessionKey, expectedSessionId, 0);
  _scheduleEmbeddedTerminalFit();
}

function _openEmbeddedTerminalSocket(cell, sessionKey, expectedSessionId, attempt) {
  if (_embeddedTerminalSessionKey !== sessionKey) return;
  var socket = new WebSocket(_embeddedTerminalUrl(cell));
  _embeddedTerminalWs = socket;
  function isCurrentSessionMessage(msg) {
    if (_embeddedTerminalSessionKey !== sessionKey) return false;
    if (_embeddedTerminalWs !== socket) return false;
    if (msg && typeof msg.session_id === 'string' && msg.session_id !== expectedSessionId) {
      return false;
    }
    return true;
  }
  socket.onopen = function() {
    if (!isCurrentSessionMessage()) return;
    var status = document.querySelector('#terminal-workspace .terminal-statusbar');
    if (status && typeof status.removeAttribute === 'function') {
      status.removeAttribute('data-closed');
    }
    _scheduleEmbeddedTerminalFit();
    focusEmbeddedTerminalWorkspace(false);
  };
  socket.onmessage = function(event) {
    var msg;
    try { msg = JSON.parse(event.data); } catch (e) { return; }
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
  socket.onclose = function() {
    if (_embeddedTerminalSessionKey !== sessionKey) return;
    if (_embeddedTerminalWs !== socket) return;
    var status = document.querySelector('#terminal-workspace .terminal-statusbar');
    if (status) status.setAttribute('data-closed', '1');
    // The main `/ws` socket delivers state updates out-of-band; check
    // whether the session is still the one the current cell points to.
    // If the cell's session_id changed (stopped, relaunched to a new id),
    // bail out — renderTerminalWorkspace() will set up a fresh surface.
    if (state && state.agents && state.agents[cell.id]) {
      var currentSid = state.agents[cell.id].session_id || '';
      if (currentSid !== expectedSessionId) return;
    } else {
      return;
    }
    if (attempt >= _EMBEDDED_TERMINAL_MAX_RETRIES) return;
    setTimeout(function() {
      if (_embeddedTerminalSessionKey !== sessionKey) return;
      _openEmbeddedTerminalSocket(cell, sessionKey, expectedSessionId, attempt + 1);
    }, _EMBEDDED_TERMINAL_RETRY_MS);
  };
  socket.onerror = function() { /* close will fire after */ };
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

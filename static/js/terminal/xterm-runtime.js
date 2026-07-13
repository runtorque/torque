/* Terminal module: xterm runtime. */

function _xtermScrollbackFromSettings(settings) {
  var raw = settings && settings.xterm_scrollback;
  var value = Number(raw);
  if (!Number.isFinite(value)) return XTERM_SCROLLBACK_DEFAULT;
  value = Math.floor(value);
  if (value < XTERM_SCROLLBACK_MIN || value > XTERM_SCROLLBACK_MAX) {
    return XTERM_SCROLLBACK_DEFAULT;
  }
  return value;
}

function _currentXtermScrollback() {
  return _xtermScrollbackFromSettings(
    state && state.global_settings ? state.global_settings : null
  );
}

function _applyEmbeddedTerminalScrollbackFromSettings() {
  const scrollback = _currentXtermScrollback();
  if (_lastAppliedXtermScrollback === scrollback) return;
  let applied = false;
  for (const key in _embeddedTerminalSessions) {
    const entry = _embeddedTerminalSessions[key];
    if (entry && entry.terminal && entry.terminal.options) {
      entry.terminal.options.scrollback = scrollback;
      applied = true;
    }
  }
  if (!applied && _embeddedTerminal && _embeddedTerminal.options) {
    _embeddedTerminal.options.scrollback = scrollback;
  }
  _lastAppliedXtermScrollback = scrollback;
}

function _setActiveEmbeddedTerminalEntry(entry) {
  _embeddedTerminal = entry ? entry.terminal : null;
  _embeddedTerminalFit = entry ? entry.fit : null;
  _embeddedTerminalWs = entry ? entry.ws : null;
  _embeddedTerminalSessionKey = entry ? entry.sessionKey : '';
  _embeddedTerminalResizeObserver = entry ? entry.resizeObserver : null;
  _embeddedTerminalDataHandler = entry ? entry.dataHandler : null;
  _embeddedTerminalDropSurface = entry ? entry.dropSurface : null;
  _embeddedTerminalDropHandlers = entry ? entry.dropHandlers : null;
  _embeddedTerminalDropDepth = entry ? (entry.dropDepth || 0) : 0;
}

function _isEmbeddedTerminalEntryActive(entry) {
  return !!(entry
    && _embeddedTerminalSessionKey === entry.sessionKey
    && _embeddedTerminalSessions[entry.sessionKey] === entry);
}

function _disposeEmbeddedTerminalEntry(entry) {
  if (!entry) return;
  if (_embeddedTerminalSessions[entry.sessionKey] === entry) {
    delete _embeddedTerminalSessions[entry.sessionKey];
  }
  _detachEmbeddedTerminalTailControls(entry);
  if (entry.dropSurface && entry.dropHandlers
      && typeof entry.dropSurface.removeEventListener === 'function') {
    entry.dropSurface.removeEventListener('dragenter', entry.dropHandlers.dragenter, true);
    entry.dropSurface.removeEventListener('dragover', entry.dropHandlers.dragover, true);
    entry.dropSurface.removeEventListener('dragleave', entry.dropHandlers.dragleave, true);
    entry.dropSurface.removeEventListener('drop', entry.dropHandlers.drop, true);
    _setEmbeddedTerminalDropTarget(entry.dropSurface, false);
  }
  if (entry.resizeObserver) entry.resizeObserver.disconnect();
  if (entry.ws) {
    entry.ws.onopen = null;
    entry.ws.onmessage = null;
    entry.ws.onerror = null;
    entry.ws.onclose = null;
    entry.ws.close();
  }
  if (entry.dataHandler && typeof entry.dataHandler.dispose === 'function') {
    entry.dataHandler.dispose();
  }
  if (entry.terminal) entry.terminal.dispose();
  if (entry.surface && typeof entry.surface.remove === 'function') entry.surface.remove();
  if (_embeddedTerminalSessionKey === entry.sessionKey) _setActiveEmbeddedTerminalEntry(null);
}

function _closeEmbeddedTerminalEntrySocket(entry) {
  if (!entry || !entry.ws) return;
  const socket = entry.ws;
  socket.onopen = null;
  socket.onmessage = null;
  socket.onerror = null;
  socket.onclose = null;
  socket.close();
  if (entry.ws === socket) entry.ws = null;
  if (_embeddedTerminalWs === socket) _embeddedTerminalWs = null;
}

function _findEmbeddedTerminalEntryForCell(cellId) {
  let fallback = null;
  for (const key of Object.keys(_embeddedTerminalSessions)) {
    const entry = _embeddedTerminalSessions[key];
    if (!entry || entry.cellId !== cellId) continue;
    if (state.agents[cellId] && _terminalCellIsTombstoned(state.agents[cellId])) continue;
    if (_embeddedTerminalSessionKey === entry.sessionKey) return entry;
    if (!fallback) fallback = entry;
  }
  return fallback;
}

function _disposeEmbeddedTerminalEntriesForCell(cellId, keepSessionKey) {
  for (const key of Object.keys(_embeddedTerminalSessions)) {
    const entry = _embeddedTerminalSessions[key];
    if (entry && entry.cellId === cellId && key !== keepSessionKey) {
      _disposeEmbeddedTerminalEntry(entry);
    }
  }
}

function _disposeEmbeddedTerminal() {
  for (const key of Object.keys(_embeddedTerminalSessions)) {
    _disposeEmbeddedTerminalEntry(_embeddedTerminalSessions[key]);
  }
  if (_embeddedTerminalDropSurface && _embeddedTerminalDropHandlers
      && typeof _embeddedTerminalDropSurface.removeEventListener === 'function') {
    _embeddedTerminalDropSurface.removeEventListener('dragenter', _embeddedTerminalDropHandlers.dragenter, true);
    _embeddedTerminalDropSurface.removeEventListener('dragover', _embeddedTerminalDropHandlers.dragover, true);
    _embeddedTerminalDropSurface.removeEventListener('dragleave', _embeddedTerminalDropHandlers.dragleave, true);
    _embeddedTerminalDropSurface.removeEventListener('drop', _embeddedTerminalDropHandlers.drop, true);
    _setEmbeddedTerminalDropTarget(_embeddedTerminalDropSurface, false);
  }
  if (_embeddedTerminalResizeObserver) _embeddedTerminalResizeObserver.disconnect();
  if (_embeddedTerminalWs) {
    _embeddedTerminalWs.onopen = null;
    _embeddedTerminalWs.onmessage = null;
    _embeddedTerminalWs.onerror = null;
    _embeddedTerminalWs.onclose = null;
    _embeddedTerminalWs.close();
  }
  if (_embeddedTerminalDataHandler && typeof _embeddedTerminalDataHandler.dispose === 'function') {
    _embeddedTerminalDataHandler.dispose();
  }
  if (_embeddedTerminal) _embeddedTerminal.dispose();
  _setActiveEmbeddedTerminalEntry(null);
  _embeddedTerminalSessionKey = '';
  _embeddedTerminalPendingFocusKey = '';
}

function _deactivateEmbeddedTerminalWorkspace() {
  _setActiveEmbeddedTerminalEntry(null);
  _embeddedTerminalPendingFocusKey = '';
}

function _embeddedTerminalUrl(cell) {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var url = protocol + '//' + location.host + '/ws/terminal/' + encodeURIComponent(cell.id);
  var clientId = '';
  try {
    if (typeof _torqueClientId === 'function') clientId = _torqueClientId();
  } catch (_err) {}
  if (!clientId) {
    try {
      if (typeof TORQUE_CLIENT_ID !== 'undefined') clientId = TORQUE_CLIENT_ID;
    } catch (_err2) {}
  }
  if (!clientId) return url;
  return url + '?client_id=' + encodeURIComponent(clientId);
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

function _embeddedTerminalDraggedImagesVisible(dataTransfer) {
  if (dataTransfer && dataTransfer.items && typeof dataTransfer.items.length === 'number') {
    var sawFile = false;
    for (var i = 0; i < dataTransfer.items.length; i++) {
      var item = dataTransfer.items[i];
      if (!item || item.kind !== 'file') continue;
      sawFile = true;
      if (item.type && item.type.indexOf('image/') === 0) return true;
    }
    if (sawFile) return false;
  }
  return _embeddedTerminalDroppedImages(dataTransfer).length > 0
    || _embeddedTerminalHasDraggedFiles(dataTransfer);
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

function _attachEmbeddedTerminalDropHandlers(cell, surface, entry) {
  if (!surface || typeof surface.addEventListener !== 'function') return;
  entry.cell = cell;
  entry.dropSurface = surface;
  entry.dropDepth = 0;
  entry.dropHandlers = {
    dragenter: function(e) {
      if (!_embeddedTerminalHasDraggedFiles(e && e.dataTransfer)) return;
      entry.dropDepth += 1;
      _setEmbeddedTerminalDropTarget(
        surface, _embeddedTerminalDraggedImagesVisible(e.dataTransfer));
    },
    dragover: function(e) {
      if (!_embeddedTerminalHasDraggedFiles(e && e.dataTransfer)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
      _setEmbeddedTerminalDropTarget(
        surface, _embeddedTerminalDraggedImagesVisible(e.dataTransfer));
    },
    dragleave: function(e) {
      if (!_embeddedTerminalHasDraggedFiles(e && e.dataTransfer)) return;
      entry.dropDepth = Math.max(0, entry.dropDepth - 1);
      if (entry.dropDepth > 0) return;
      if (e.relatedTarget && typeof surface.contains === 'function' && surface.contains(e.relatedTarget)) {
        return;
      }
      _setEmbeddedTerminalDropTarget(surface, false);
    },
    drop: async function(e) {
      var files = _embeddedTerminalDroppedFiles(e && e.dataTransfer);
      if (!files.length) return;
      e.preventDefault();
      entry.dropDepth = 0;
      _setEmbeddedTerminalDropTarget(surface, false);
      var sessionKey = entry.sessionKey;
      var images = _embeddedTerminalDroppedImages(e.dataTransfer);
      if (!images.length) {
        if (_isEmbeddedTerminalEntryActive(entry)) {
          _embeddedTerminalPendingFocusKey = sessionKey;
          focusEmbeddedTerminalWorkspace(false);
        }
        return;
      }
      var paths = await _uploadEmbeddedTerminalImages(entry.cell || cell, images);
      if (!_isEmbeddedTerminalEntryActive(entry)) return;
      if (paths.length && entry.ws && entry.ws.readyState === WebSocket.OPEN) {
        entry.ws.send(JSON.stringify({
          type: 'input',
          data: paths.map(_shellQuoteTerminalPath).join(' ') + ' ',
        }));
      }
      _embeddedTerminalPendingFocusKey = sessionKey;
      focusEmbeddedTerminalWorkspace(false);
    },
  };
  // Capture-phase listeners ensure the workspace still sees file drags
  // before xterm's helper textarea can swallow them.
  surface.addEventListener('dragenter', entry.dropHandlers.dragenter, true);
  surface.addEventListener('dragover', entry.dropHandlers.dragover, true);
  surface.addEventListener('dragleave', entry.dropHandlers.dragleave, true);
  surface.addEventListener('drop', entry.dropHandlers.drop, true);
  if (_isEmbeddedTerminalEntryActive(entry)) _setActiveEmbeddedTerminalEntry(entry);
}

function _updateEmbeddedTerminalEntrySession(entry, cell, sessionKey, sessionId) {
  const oldSessionKey = entry.sessionKey || '';
  if (oldSessionKey && oldSessionKey !== sessionKey
      && _embeddedTerminalSessions[oldSessionKey] === entry) {
    delete _embeddedTerminalSessions[oldSessionKey];
  }
  entry.sessionKey = sessionKey;
  entry.cellId = cell.id;
  entry.cell = cell;
  entry.sessionId = sessionId;
  if (typeof entry.tailPinned !== 'boolean') entry.tailPinned = true;
  _embeddedTerminalSessions[sessionKey] = entry;
  if (entry.surface) {
    if (entry.surface.dataset) entry.surface.dataset.torqueSessionKey = sessionKey;
    if (typeof entry.surface.setAttribute === 'function') {
      entry.surface.setAttribute('data-torque-session-key', sessionKey);
    }
  }
}

function _writeEmbeddedTerminalSessionRestartedSeparator(entry) {
  const term = entry && entry.terminal;
  if (!term) return;
  const line = '──── Torque session restarted ────';
  if (typeof term.writeln === 'function') {
    term.writeln(line);
  } else if (typeof term.write === 'function') {
    term.write('\r\n' + line + '\r\n');
  }
  if (typeof term.write === 'function') {
    const rows = Math.max(1, Math.min(200, Number(term.rows) || 24));
    term.write(new Array(rows + 1).join('\r\n'));
  }
}

function _embeddedTerminalViewport(entry) {
  const surface = entry && entry.surface;
  return surface && typeof surface.querySelector === 'function'
    ? surface.querySelector('.xterm-viewport')
    : null;
}

function _embeddedTerminalViewportAtTail(viewport) {
  if (!viewport) return true;
  const scrollTop = Number(viewport.scrollTop) || 0;
  const clientHeight = Number(viewport.clientHeight) || 0;
  const scrollHeight = Number(viewport.scrollHeight) || 0;
  return scrollHeight <= clientHeight
    || (scrollHeight - scrollTop - clientHeight) <= EMBEDDED_TERMINAL_TAIL_THRESHOLD_PX;
}

function _embeddedTerminalViewportTailDistance(viewport) {
  if (!viewport) return 0;
  const scrollTop = Number(viewport.scrollTop) || 0;
  const clientHeight = Number(viewport.clientHeight) || 0;
  const scrollHeight = Number(viewport.scrollHeight) || 0;
  return Math.max(0, scrollHeight - scrollTop - clientHeight);
}

function _embeddedTerminalBufferTailDistancePx(entry) {
  const term = entry && entry.terminal;
  const buffer = term && term.buffer && term.buffer.active;
  if (!buffer) return 0;
  const viewportY = Number(buffer.viewportY);
  const baseY = Number(buffer.baseY);
  if (!Number.isFinite(viewportY) || !Number.isFinite(baseY)) return 0;
  const rows = Math.max(0, baseY - viewportY);
  if (!rows) return 0;
  const lineHeight = Math.max(1, Number(term && term.options && term.options.fontSize) || 13);
  return rows * lineHeight;
}

function _embeddedTerminalScrolledUpBeyondTailDetachThreshold(entry) {
  const viewport = _embeddedTerminalViewport(entry);
  const viewportDistance = _embeddedTerminalViewportTailDistance(viewport);
  const bufferDistance = _embeddedTerminalBufferTailDistancePx(entry);
  return Math.max(viewportDistance, bufferDistance)
    >= EMBEDDED_TERMINAL_SCROLL_UP_DETACH_THRESHOLD_PX;
}

function _embeddedTerminalAtTail(entry) {
  const term = entry && entry.terminal;
  const buffer = term && term.buffer && term.buffer.active;
  let bufferKnown = false;
  let bufferAtTail = true;
  if (buffer) {
    const viewportY = Number(buffer.viewportY);
    const baseY = Number(buffer.baseY);
    if (Number.isFinite(viewportY) && Number.isFinite(baseY)) {
      bufferKnown = true;
      bufferAtTail = baseY <= viewportY;
    }
  }
  const viewport = _embeddedTerminalViewport(entry);
  const viewportAtTail = _embeddedTerminalViewportAtTail(viewport);
  return bufferKnown ? (bufferAtTail && viewportAtTail) : viewportAtTail;
}

function _embeddedTerminalTailPinned(entry) {
  if (!entry) return false;
  if (typeof entry.tailPinned === 'boolean') return entry.tailPinned;
  return _embeddedTerminalAtTail(entry);
}

function _updateEmbeddedTerminalTailButton(entry) {
  const button = entry && entry.tailButton;
  if (!button) return;
  const hidden = _embeddedTerminalTailPinned(entry) && _embeddedTerminalAtTail(entry);
  const nextHidden = !!hidden;
  if (button.hidden !== nextHidden) button.hidden = nextHidden;
  if (typeof button.setAttribute === 'function') {
    const ariaHidden = hidden ? 'true' : 'false';
    if (typeof button.getAttribute !== 'function'
        || button.getAttribute('aria-hidden') !== ariaHidden) {
      button.setAttribute('aria-hidden', ariaHidden);
    }
  }
}

function _embeddedTerminalSetTailPinned(entry, pinned) {
  if (!entry) return;
  entry.tailPinned = !!pinned;
  if (entry.tailPinned) {
    entry.wheelScrollUpIntentPx = 0;
    entry.wheelScrollUpIntentAt = 0;
  }
  _updateEmbeddedTerminalTailButton(entry);
}

function _embeddedTerminalMarkUserScrollIntent(entry) {
  if (!entry) return;
  entry.userScrollIntentUntil = Date.now() + EMBEDDED_TERMINAL_USER_SCROLL_INTENT_MS;
}

function _embeddedTerminalHasUserScrollIntent(entry) {
  if (!entry) return false;
  if (entry.userScrollPointerActive) return true;
  return Number(entry.userScrollIntentUntil || 0) >= Date.now();
}

function _syncEmbeddedTerminalTailPinnedFromViewport(entry, userInitiated) {
  if (!entry) return;
  if (_embeddedTerminalAtTail(entry)) {
    _embeddedTerminalSetTailPinned(entry, true);
  } else if (userInitiated || (
      _embeddedTerminalHasUserScrollIntent(entry)
      && _embeddedTerminalScrolledUpBeyondTailDetachThreshold(entry)
    )) {
    _embeddedTerminalSetTailPinned(entry, false);
  } else {
    _updateEmbeddedTerminalTailButton(entry);
  }
}

function _embeddedTerminalWheelDeltaYPx(event, viewport) {
  const raw = Number(event && event.deltaY);
  if (!Number.isFinite(raw)) return 0;
  const mode = Number(event && event.deltaMode) || 0;
  if (mode === 1) return raw * 16;
  if (mode === 2) return raw * Math.max(1, Number(viewport && viewport.clientHeight) || 240);
  return raw;
}

function _embeddedTerminalResetWheelScrollUpIntent(entry) {
  if (!entry) return;
  entry.wheelScrollUpIntentPx = 0;
  entry.wheelScrollUpIntentAt = 0;
}

function _embeddedTerminalMarkWheelIntent(entry, event, viewport) {
  if (!entry) return;
  _embeddedTerminalMarkUserScrollIntent(entry);
  const deltaY = _embeddedTerminalWheelDeltaYPx(event, viewport);
  if (deltaY >= 0) {
    _embeddedTerminalResetWheelScrollUpIntent(entry);
    return;
  }
  const now = Date.now();
  const previousAt = Number(entry.wheelScrollUpIntentAt || 0);
  const previousPx = previousAt && (now - previousAt) <= EMBEDDED_TERMINAL_WHEEL_INTENT_RESET_MS
    ? Number(entry.wheelScrollUpIntentPx || 0)
    : 0;
  const nextPx = previousPx + Math.abs(deltaY);
  entry.wheelScrollUpIntentPx = nextPx;
  entry.wheelScrollUpIntentAt = now;
  if (nextPx >= EMBEDDED_TERMINAL_SCROLL_UP_DETACH_THRESHOLD_PX) {
    _embeddedTerminalSetTailPinned(entry, false);
  }
}

function _ensureEmbeddedTerminalTailButton(entry) {
  const surface = entry && entry.surface;
  if (!surface || !document.createElement) return null;
  let button = entry.tailButton || null;
  if (!button || button.parentNode !== surface) {
    button = document.createElement('button');
    button.type = 'button';
    button.className = 'terminal-tail-button';
    if (button.classList && typeof button.classList.add === 'function') {
      button.classList.add('terminal-tail-button');
    }
    button.title = 'Scroll to bottom';
    button.textContent = '↓';
    if (typeof button.setAttribute === 'function') {
      button.setAttribute('aria-label', 'Scroll terminal to bottom');
    }
    if (typeof button.addEventListener === 'function') {
      button.addEventListener('click', function(event) {
        if (event && typeof event.preventDefault === 'function') event.preventDefault();
        if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
        _embeddedTerminalScrollToTail(entry);
        if (entry && entry.sessionKey) _embeddedTerminalPendingFocusKey = entry.sessionKey;
        focusEmbeddedTerminalWorkspace(false);
      });
    }
    if (typeof surface.appendChild === 'function') surface.appendChild(button);
    entry.tailButton = button;
  }
  _updateEmbeddedTerminalTailButton(entry);
  return button;
}

function _detachEmbeddedTerminalTailControls(entry) {
  const controls = entry && entry.tailControls;
  if (!controls) return;
  const viewport = controls.viewport;
  const surface = controls.surface;
  const handlers = controls.handlers || {};
  if (viewport && typeof viewport.removeEventListener === 'function') {
    viewport.removeEventListener('wheel', handlers.wheelIntent || handlers.userIntent);
    viewport.removeEventListener('touchstart', handlers.userIntent);
    viewport.removeEventListener('pointerdown', handlers.pointerDown);
    viewport.removeEventListener('scroll', handlers.scroll);
  }
  if (surface && typeof surface.removeEventListener === 'function') {
    surface.removeEventListener('keydown', handlers.userIntentKey);
  }
  if (typeof document !== 'undefined' && document && typeof document.removeEventListener === 'function') {
    document.removeEventListener('pointerup', handlers.pointerUp);
    document.removeEventListener('pointercancel', handlers.pointerUp);
  }
  entry.tailControls = null;
}

function _attachEmbeddedTerminalTailControls(entry) {
  if (!entry) return;
  _ensureEmbeddedTerminalTailButton(entry);
  const viewport = _embeddedTerminalViewport(entry);
  if (!viewport || typeof viewport.addEventListener !== 'function') {
    _updateEmbeddedTerminalTailButton(entry);
    return;
  }
  const surface = entry.surface || null;
  if (entry.tailControls && entry.tailControls.viewport === viewport
      && entry.tailControls.surface === surface) {
    _updateEmbeddedTerminalTailButton(entry);
    return;
  }
  _detachEmbeddedTerminalTailControls(entry);
  const userIntent = function() {
    _embeddedTerminalMarkUserScrollIntent(entry);
    if (typeof setTimeout === 'function') {
      setTimeout(function() {
        _syncEmbeddedTerminalTailPinnedFromViewport(entry, false);
      }, 0);
    }
  };
  const wheelIntent = function(event) {
    _embeddedTerminalMarkWheelIntent(entry, event, viewport);
  };
  const userIntentKey = function(event) {
    const key = event && (event.key || event.code);
    if (key === 'PageUp' || key === 'PageDown' || key === 'Home' || key === 'End'
        || key === 'ArrowUp' || key === 'ArrowDown' || key === 'Up' || key === 'Down') {
      userIntent();
    }
  };
  const pointerDown = function() {
    entry.userScrollPointerActive = true;
    _embeddedTerminalMarkUserScrollIntent(entry);
  };
  const pointerUp = function() {
    entry.userScrollPointerActive = false;
    _syncEmbeddedTerminalTailPinnedFromViewport(entry, true);
  };
  const scroll = function() {
    _syncEmbeddedTerminalTailPinnedFromViewport(entry, false);
  };
  viewport.addEventListener('wheel', wheelIntent, { passive: true });
  viewport.addEventListener('touchstart', userIntent, { passive: true });
  viewport.addEventListener('pointerdown', pointerDown, true);
  viewport.addEventListener('scroll', scroll);
  if (surface && typeof surface.addEventListener === 'function') {
    surface.addEventListener('keydown', userIntentKey, true);
  }
  if (typeof document !== 'undefined' && document && typeof document.addEventListener === 'function') {
    document.addEventListener('pointerup', pointerUp, true);
    document.addEventListener('pointercancel', pointerUp, true);
  }
  entry.tailControls = {
    viewport: viewport,
    surface: surface,
    handlers: {
      userIntent: userIntent,
      wheelIntent: wheelIntent,
      userIntentKey: userIntentKey,
      pointerDown: pointerDown,
      pointerUp: pointerUp,
      scroll: scroll,
    },
  };
  _updateEmbeddedTerminalTailButton(entry);
}

function _embeddedTerminalTailSnapshot(entry) {
  const term = entry && entry.terminal;
  const buffer = term && term.buffer && term.buffer.active;
  const viewportY = buffer ? Number(buffer.viewportY) : NaN;
  const viewport = _embeddedTerminalViewport(entry);
  return {
    atTail: _embeddedTerminalTailPinned(entry),
    viewportY: Number.isFinite(viewportY) ? viewportY : null,
    scrollTop: viewport ? (Number(viewport.scrollTop) || 0) : null,
  };
}

function _embeddedTerminalStillPinned(entry, snapshot) {
  if (!snapshot || !snapshot.atTail) return false;
  if (!_embeddedTerminalTailPinned(entry)) return false;
  const term = entry && entry.terminal;
  const buffer = term && term.buffer && term.buffer.active;
  if (buffer && snapshot.viewportY !== null) {
    const viewportY = Number(buffer.viewportY);
    if (Number.isFinite(viewportY)) return viewportY >= snapshot.viewportY;
  }
  const viewport = _embeddedTerminalViewport(entry);
  if (viewport && snapshot.scrollTop !== null) {
    return (Number(viewport.scrollTop) || 0) >= snapshot.scrollTop;
  }
  return true;
}

function _embeddedTerminalScrollToTail(entry) {
  const term = entry && entry.terminal;
  if (term && typeof term.scrollToBottom === 'function') {
    term.scrollToBottom();
  } else {
    const viewport = _embeddedTerminalViewport(entry);
    if (viewport) {
      viewport.scrollTop = Math.max(
        0,
        (Number(viewport.scrollHeight) || 0) - (Number(viewport.clientHeight) || 0)
      );
    }
  }
  _embeddedTerminalSetTailPinned(entry, true);
  const viewport = _embeddedTerminalViewport(entry);
  if (viewport) {
    viewport.scrollTop = Math.max(
      0,
      (Number(viewport.scrollHeight) || 0) - (Number(viewport.clientHeight) || 0)
    );
  }
  _updateEmbeddedTerminalTailButton(entry);
}

function _scheduleEmbeddedTerminalScrollToTail(entry) {
  if (!entry) return;
  // Defer past the fit() that activation also schedules so the scroll lands
  // after xterm has reflowed to the current stage size. requestAnimationFrame
  // callbacks fire in order, so the fit runs first.
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(function() {
      if (!_isEmbeddedTerminalEntryActive(entry)) return;
      _embeddedTerminalScrollToTail(entry);
    });
  } else {
    _embeddedTerminalScrollToTail(entry);
  }
}

function _writeEmbeddedTerminalData(entry, data) {
  const term = entry && entry.terminal;
  if (!term || !data || typeof term.write !== 'function') return;
  const tailSnapshot = _embeddedTerminalTailSnapshot(entry);
  let restored = false;
  function restoreTailIfPinned() {
    if (restored) return;
    restored = true;
    if (_embeddedTerminalStillPinned(entry, tailSnapshot)) {
      _embeddedTerminalScrollToTail(entry);
    } else {
      _updateEmbeddedTerminalTailButton(entry);
    }
  }
  term.write(data, restoreTailIfPinned);
  if (term.write.length < 2 && typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(restoreTailIfPinned);
  }
}

function _sanitizeEmbeddedTerminalRekeySnapshot(data) {
  return String(data || '')
    // TUIs often start a fresh session with a hard reset and display-clear
    // sequence. During a same-cell re-key those clears would erase the
    // preserved scrollback we intentionally kept, so suppress them only for
    // the first snapshot after the session swap.
    .replace(/\x1bc/g, '')
    .replace(/\x1b\[[0-?]*[ -/]*J/g, '');
}

function _scheduleEmbeddedTerminalFit(entry, opts) {
  entry = entry || _embeddedTerminalSessions[_embeddedTerminalSessionKey];
  if (!entry || !_isEmbeddedTerminalEntryActive(entry)
      || !entry.terminal || !entry.fit) return;
  const preserveTail = !!(opts && opts.preserveTail);
  requestAnimationFrame(function() {
    if (!_isEmbeddedTerminalEntryActive(entry) || !entry.terminal || !entry.fit) return;
    // Capture tail state from the logical buffer before the fit reflows xterm.
    // The compose box growing to multiple lines shrinks the terminal stage and
    // fires the surface ResizeObserver; without re-pinning, the reflow detaches
    // the viewport from the bottom and tail/autoscroll silently stops while the
    // user is still typing. Only re-pin when the user was already at the tail so
    // a deliberate scroll-up survives the resize.
    const wasAtTail = preserveTail && _embeddedTerminalTailPinned(entry);
    entry.fit.fit();
    _attachEmbeddedTerminalTailControls(entry);
    if (wasAtTail) _embeddedTerminalScrollToTail(entry);
    else _updateEmbeddedTerminalTailButton(entry);
    const cols = entry.terminal.cols;
    const rows = entry.terminal.rows;
    if (entry.lastSentCols === cols && entry.lastSentRows === rows) return;
    if (entry.ws && entry.ws.readyState === WebSocket.OPEN) {
      entry.ws.send(JSON.stringify({
        type: 'resize',
        cols: cols,
        rows: rows,
      }));
      entry.ws.send(JSON.stringify({ type: 'focus' }));
      entry.lastSentCols = cols;
      entry.lastSentRows = rows;
    }
  });
}

function _embeddedTerminalObservedSizeChanged(entry, resizeEntries) {
  var roEntry = resizeEntries && resizeEntries.length ? resizeEntries[0] : null;
  var box = roEntry && roEntry.contentBoxSize;
  if (Array.isArray(box)) box = box[0];
  var width = box && typeof box.inlineSize === 'number'
    ? box.inlineSize : roEntry && roEntry.contentRect && roEntry.contentRect.width;
  var height = box && typeof box.blockSize === 'number'
    ? box.blockSize : roEntry && roEntry.contentRect && roEntry.contentRect.height;
  if (typeof width !== 'number' || typeof height !== 'number') return true;
  var changed = entry.lastObservedWidth !== width || entry.lastObservedHeight !== height;
  entry.lastObservedWidth = width;
  entry.lastObservedHeight = height;
  return changed;
}

// Max attempts to re-open the terminal WS after a drop (e.g. daemon
// restart). Each attempt sleeps _EMBEDDED_TERMINAL_RETRY_MS between
// tries, giving ~15s of cover for a standalone-mode restart.
var _EMBEDDED_TERMINAL_RETRY_MS = 1000;
var _EMBEDDED_TERMINAL_MAX_RETRIES = 15;

function _connectEmbeddedTerminal(cell, surface) {
  var expectedSessionId = cell.session_id || '';
  var sessionKey = cell.id + ':' + expectedSessionId;
  var existingEntry = _embeddedTerminalSessions[sessionKey]
    || _findEmbeddedTerminalEntryForCell(cell.id);
  if (existingEntry) {
    var oldSessionKey = existingEntry.sessionKey || '';
    var sessionChanged = oldSessionKey !== sessionKey
      || (existingEntry.sessionId || '') !== expectedSessionId;
    _closeEmbeddedTerminalEntrySocket(existingEntry);
    _updateEmbeddedTerminalEntrySession(existingEntry, cell, sessionKey, expectedSessionId);
    _disposeEmbeddedTerminalEntriesForCell(cell.id, sessionKey);
    if (surface && existingEntry.surface && surface !== existingEntry.surface
        && typeof surface.remove === 'function') {
      surface.remove();
    }
    if (sessionChanged) {
      _writeEmbeddedTerminalSessionRestartedSeparator(existingEntry);
      existingEntry.appendNextSnapshot = true;
    } else {
      existingEntry.appendNextSnapshot = false;
    }
    _embeddedTerminalPendingFocusKey = sessionKey;
    _setActiveEmbeddedTerminalEntry(existingEntry);
    _openEmbeddedTerminalSocket(cell, sessionKey, expectedSessionId, 0, existingEntry);
    _scheduleEmbeddedTerminalFit(existingEntry);
    return;
  }
  var entry = {
    sessionKey: sessionKey,
    cellId: cell.id,
    sessionId: expectedSessionId,
    surface: surface,
    tailPinned: true,
  };
  _embeddedTerminalSessions[sessionKey] = entry;
  _embeddedTerminalPendingFocusKey = sessionKey;
  entry.terminal = new Terminal({
    allowProposedApi: true,
    allowTransparency: false,
    convertEol: false,
    cursorBlink: true,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    fontSize: 13,
    lineHeight: 1.0,
    letterSpacing: 0,
    scrollback: _currentXtermScrollback(),
    theme: {
      background: '#0d1117',
      foreground: '#e6edf3',
      cursor: '#58a6ff',
      selectionBackground: 'rgba(88,166,255,0.28)',
    },
  });
  entry.fit = new FitAddon.FitAddon();
  entry.terminal.loadAddon(entry.fit);
  entry.terminal.open(surface);
  _setActiveEmbeddedTerminalEntry(entry);
  _attachEmbeddedTerminalTailControls(entry);
  _attachEmbeddedTerminalDropHandlers(cell, surface, entry);
  try { entry.fit.fit(); } catch (e) { /* container not measurable yet */ }
  // Shift+Enter → send LF so TUIs like Codex and Claude Code treat it as a
  // soft newline instead of submitting. xterm.js default maps Shift+Enter
  // to plain Enter; consume every matching key event so the follow-up keypress
  // cannot emit a stray `\r`.
  if (typeof entry.terminal.attachCustomKeyEventHandler === 'function') {
    entry.terminal.attachCustomKeyEventHandler(function(e) {
      if (e.key === 'Enter' && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (e.type === 'keydown' && entry.ws && entry.ws.readyState === WebSocket.OPEN) {
          entry.ws.send(JSON.stringify({ type: 'input', data: '\n' }));
        }
        return false;
      }
      if (e.type !== 'keydown') return true;
      return true;
    });
  }
  entry.dataHandler = entry.terminal.onData(function(data) {
    if (entry.ws && entry.ws.readyState === WebSocket.OPEN) {
      entry.ws.send(JSON.stringify({ type: 'input', data: data }));
    }
  });
  entry.resizeObserver = new ResizeObserver(function(entries) {
    if (!_embeddedTerminalObservedSizeChanged(entry, entries)) return;
    _scheduleEmbeddedTerminalFit(entry, { preserveTail: true });
  });
  entry.resizeObserver.observe(surface);
  _setActiveEmbeddedTerminalEntry(entry);
  _openEmbeddedTerminalSocket(cell, sessionKey, expectedSessionId, 0, entry);
  _scheduleEmbeddedTerminalFit(entry);
}

function _openEmbeddedTerminalSocket(cell, sessionKey, expectedSessionId, attempt, entry) {
  if (_embeddedTerminalSessions[sessionKey] !== entry) return;
  var socket = new WebSocket(_embeddedTerminalUrl(cell));
  entry.ws = socket;
  entry.lastSentCols = null;
  entry.lastSentRows = null;
  if (_isEmbeddedTerminalEntryActive(entry)) _embeddedTerminalWs = socket;
  function isCurrentSessionMessage(msg) {
    if (_embeddedTerminalSessions[sessionKey] !== entry) return false;
    if (entry.ws !== socket) return false;
    if (msg && typeof msg.session_id === 'string' && msg.session_id !== expectedSessionId) {
      return false;
    }
    return true;
  }
  socket.onopen = function() {
    if (!isCurrentSessionMessage()) return;
    if (_isEmbeddedTerminalEntryActive(entry)) {
      var status = document.querySelector('#terminal-workspace .terminal-statusbar');
      if (status && typeof status.removeAttribute === 'function') {
        status.removeAttribute('data-closed');
      }
      _scheduleEmbeddedTerminalFit(entry);
      focusEmbeddedTerminalWorkspace(false);
    }
  };
  socket.onmessage = function(event) {
    var msg;
    try { msg = JSON.parse(event.data); } catch (e) { return; }
    if (!isCurrentSessionMessage(msg)) return;
    if (msg.type === 'snapshot') {
      if (entry.appendNextSnapshot) {
        entry.appendNextSnapshot = false;
        if (msg.data) msg.data = _sanitizeEmbeddedTerminalRekeySnapshot(msg.data);
      } else {
        entry.terminal.reset();
      }
      if (msg.data) _writeEmbeddedTerminalData(entry, msg.data);
      if (_isEmbeddedTerminalEntryActive(entry)) {
        _scheduleEmbeddedTerminalFit(entry);
        focusEmbeddedTerminalWorkspace(false);
      }
    } else if (msg.type === 'output' && msg.data) {
      _writeEmbeddedTerminalData(entry, msg.data);
    }
  };
  socket.onclose = function() {
    if (_embeddedTerminalSessions[sessionKey] !== entry) return;
    if (entry.ws !== socket) return;
    if (_isEmbeddedTerminalEntryActive(entry)) {
      var status = document.querySelector('#terminal-workspace .terminal-statusbar');
      if (status) status.setAttribute('data-closed', '1');
    }
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
      if (_embeddedTerminalSessions[sessionKey] !== entry) return;
      _openEmbeddedTerminalSocket(cell, sessionKey, expectedSessionId, attempt + 1, entry);
    }, _EMBEDDED_TERMINAL_RETRY_MS);
  };
  socket.onerror = function() { /* close will fire after */ };
}

function _pruneEmbeddedTerminalSessions() {
  if (!state || !state.agents) return;
  for (const key of Object.keys(_embeddedTerminalSessions)) {
    const entry = _embeddedTerminalSessions[key];
    const cell = entry && state.agents[entry.cellId];
    if (!cell || _terminalCellIsTombstoned(cell)) {
      _disposeEmbeddedTerminalEntry(entry);
    }
  }
}

function _clearEmbeddedTerminalStagePlaceholders(stage) {
  if (!stage || !stage.children) return;
  const children = Array.prototype.slice.call(stage.children);
  for (let i = 0; i < children.length; i++) {
    const child = children[i];
    if (child && child.classList && child.classList.contains('terminal-surface')) continue;
    if (child && typeof child.remove === 'function') {
      child.remove();
    } else if (stage && typeof stage.removeChild === 'function') {
      stage.removeChild(child);
    }
  }
}

function _createEmbeddedTerminalSurface(stage, sessionKey) {
  _clearEmbeddedTerminalStagePlaceholders(stage);
  let surface = stage && stage.querySelector ? stage.querySelector('.terminal-surface') : null;
  if (surface && surface.dataset && surface.dataset.torqueSessionKey) surface = null;
  if (!surface) {
    surface = document.createElement('div');
    if (stage && typeof stage.appendChild === 'function') stage.appendChild(surface);
  }
  surface.className = 'terminal-surface';
  if (surface.classList && typeof surface.classList.add === 'function') {
    surface.classList.add('terminal-surface');
  }
  if (surface.dataset) surface.dataset.torqueSessionKey = sessionKey;
  if (typeof surface.setAttribute === 'function') {
    surface.setAttribute('data-torque-session-key', sessionKey);
  }
  return surface;
}

function _activateEmbeddedTerminalSurface(stage, sessionKey, opts) {
  opts = opts || {};
  _clearEmbeddedTerminalStagePlaceholders(stage);
  const entry = _embeddedTerminalSessions[sessionKey] || null;
  // The previously active session, captured before _setActiveEmbeddedTerminalEntry
  // overwrites it, so we can tell an agent switch apart from a same-session
  // rerender (renderTerminalWorkspace runs on every grid render).
  const previousSessionKey = _embeddedTerminalSessionKey;
  for (const key in _embeddedTerminalSessions) {
    const candidate = _embeddedTerminalSessions[key];
    if (!candidate || !candidate.surface) continue;
    const active = key === sessionKey;
    if (active && stage && candidate.surface.parentNode !== stage
        && typeof stage.appendChild === 'function') {
      stage.appendChild(candidate.surface);
    }
    const nextHidden = !active;
    if (candidate.surface.hidden !== nextHidden) candidate.surface.hidden = nextHidden;
    if (candidate.surface.style) {
      const nextDisplay = active ? '' : 'none';
      if (candidate.surface.style.display !== nextDisplay) {
        candidate.surface.style.display = nextDisplay;
      }
    }
  }
  _setActiveEmbeddedTerminalEntry(entry);
  if (entry) {
    _attachEmbeddedTerminalTailControls(entry);
    // Same-session grid/workspace rerenders can happen for every state delta
    // while a user is typing a DM and an agent is producing output. Fitting
    // xterm on each rerender visibly flickers and perturbs tail/focus state.
    // Initial connections and true session switches still fit; compose/stage
    // size changes are handled by the ResizeObserver with preserveTail=true,
    // preserving PR #770's tailing semantics without coupling every keystroke
    // to a terminal reflow.
    if (previousSessionKey !== sessionKey || opts.forceFit) {
      _scheduleEmbeddedTerminalFit(entry, { preserveTail: !!opts.preserveTail });
    }
    // Switching to an agent's terminal should land at the bottom and resume
    // tailing rather than leaving the viewport pinned to the top (or wherever
    // the previous activation left it). Skip same-session rerenders so a
    // deliberate scroll-up is preserved during normal output.
    if (previousSessionKey !== sessionKey) {
      _scheduleEmbeddedTerminalScrollToTail(entry);
    }
  }
  return entry;
}

function _renderEmbeddedTerminalStagePlaceholder(stage, html) {
  if (!stage) return;
  const hasPlaceholder = !!(stage.children && Array.prototype.some.call(stage.children, function(child) {
    return !(child && child.classList && child.classList.contains('terminal-surface'));
  }));
  if (stage._torqueLastHtml === html && hasPlaceholder) return;
  _clearEmbeddedTerminalStagePlaceholders(stage);
  const placeholder = document.createElement('div');
  placeholder.className = 'terminal-empty';
  if (placeholder.classList && typeof placeholder.classList.add === 'function') {
    placeholder.classList.add('terminal-empty');
  }
  placeholder.innerHTML = html;
  if (typeof stage.appendChild === 'function') stage.appendChild(placeholder);
  stage._torqueLastHtml = html;
}

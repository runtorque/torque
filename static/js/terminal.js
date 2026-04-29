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
let _terminalComposeErrors = Object.create(null);
let _lastAppliedXtermScrollback = null;

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

function isEmbeddedTerminalMode() {
  return !!(state && state.runtime && state.runtime.embedded_terminal);
}

function _terminalCurrentGroupName() {
  if (typeof _singleGroupModeEnabled === 'function'
      && _singleGroupModeEnabled()
      && typeof _activeGroup === 'function') {
    return _activeGroup() || '';
  }
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
      + '  <div class="terminal-compose-slot"></div>'
      + '  <div class="terminal-statusbar"></div>'
      + '</div>';
    shell = root.querySelector('.terminal-shell');
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
    tabs: shell.querySelector('.terminal-tabs'),
    stage: shell.querySelector('.terminal-stage'),
    compose: compose,
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

function _terminalComposeDomId(cellId) {
  const safe = String(cellId || '')
    .replace(/[^A-Za-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return safe || 'selected';
}

function _terminalComposeInputId(cellId) {
  return 'terminal-compose-input-' + _terminalComposeDomId(cellId);
}

function _terminalComposeButtonId(cellId) {
  return 'terminal-compose-submit-' + _terminalComposeDomId(cellId);
}

function _terminalComposeContainerFor(el) {
  for (let node = el; node; node = node.parentNode) {
    if (node.classList && node.classList.contains('terminal-compose')) {
      return node;
    }
  }
  return null;
}

function _terminalComposeTextarea(root) {
  return root && root.querySelector ? root.querySelector('.terminal-compose-input') : null;
}

function _terminalComposeButtonFor(el, cellId) {
  const container = _terminalComposeContainerFor(el);
  if (container && container.querySelector) {
    const btn = container.querySelector('.terminal-compose-submit');
    if (btn) return btn;
  }
  const id = _terminalComposeButtonId(cellId || (el && el.dataset ? el.dataset.cellId : ''));
  return document.getElementById ? document.getElementById(id) : null;
}

function _terminalComposeAutoResize(el) {
  if (!el) return;
  if (typeof taskAutoResize === 'function') {
    taskAutoResize(el);
  } else if (typeof boardAddTaskAutoResize === 'function') {
    boardAddTaskAutoResize(el);
  }
}

function _terminalComposeSetButtonState(input) {
  if (!input) return;
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  const button = _terminalComposeButtonFor(input, cellId);
  if (button) button.disabled = !String(input.value || '').trim();
}

function _terminalComposeErrorElement(input) {
  const container = _terminalComposeContainerFor(input);
  return container && container.querySelector
    ? container.querySelector('.terminal-compose-error')
    : null;
}

function _terminalComposeSetError(input, message) {
  if (!input) return;
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  const text = String(message || '');
  if (cellId) _terminalComposeErrors[cellId] = text;
  const el = _terminalComposeErrorElement(input);
  if (el) el.textContent = text;
}

function _terminalComposeSetDropTarget(input, active) {
  if (!input || !input.classList) return;
  input.classList.toggle('terminal-compose-drop-target', !!active);
  const container = _terminalComposeContainerFor(input);
  if (container && container.classList) {
    container.classList.toggle('terminal-compose-drop-target', !!active);
  }
}

function _terminalComposeDroppedFiles(dataTransfer) {
  if (!dataTransfer || !dataTransfer.files || !dataTransfer.files.length) return [];
  return Array.prototype.slice.call(dataTransfer.files);
}

function _terminalComposeHasDraggedFiles(dataTransfer) {
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
  return _terminalComposeDroppedFiles(dataTransfer).length > 0;
}

function _terminalComposeNormalizeMime(type) {
  return String(type || '').split(';')[0].trim().toLowerCase();
}

function _terminalComposeFormatBytes(bytes) {
  var mb = Math.floor(bytes / (1024 * 1024));
  return (mb || 1) + ' MB';
}

function _terminalComposeFileLabel(file) {
  return file && file.name ? String(file.name) : 'Dropped file';
}

function terminalComposeValidateDroppedFiles(files) {
  var accepted = [];
  var errors = [];
  var list = Array.prototype.slice.call(files || []);
  for (var i = 0; i < list.length; i++) {
    var file = list[i];
    var mime = _terminalComposeNormalizeMime(file && file.type);
    var name = _terminalComposeFileLabel(file);
    if (!TERMINAL_COMPOSE_ATTACHMENT_MIME_TYPES[mime]) {
      errors.push(name + ' is not a supported image type.');
      continue;
    }
    if (file && typeof file.size === 'number'
        && file.size > TERMINAL_COMPOSE_ATTACHMENT_MAX_BYTES) {
      errors.push(name + ' is larger than '
        + _terminalComposeFormatBytes(TERMINAL_COMPOSE_ATTACHMENT_MAX_BYTES) + '.');
      continue;
    }
    accepted.push(file);
  }
  return { accepted: accepted, errors: errors };
}

function _terminalComposeInsertPaths(input, paths) {
  if (!input || !paths || !paths.length) return;
  var insertText = paths.map(function(path) { return String(path || ''); })
    .filter(Boolean)
    .join('\n');
  if (!insertText) return;
  var value = String(input.value || '');
  var start = typeof input.selectionStart === 'number' ? input.selectionStart : value.length;
  var end = typeof input.selectionEnd === 'number' ? input.selectionEnd : start;
  start = Math.max(0, Math.min(value.length, start));
  end = Math.max(start, Math.min(value.length, end));
  input.value = value.slice(0, start) + insertText + value.slice(end);
  var cursor = start + insertText.length;
  if (typeof input.setSelectionRange === 'function') {
    input.setSelectionRange(cursor, cursor);
  } else {
    input.selectionStart = cursor;
    input.selectionEnd = cursor;
  }
  terminalComposeInput(input);
  if (typeof input.focus === 'function') input.focus();
}

async function _terminalComposeUploadAttachments(cellId, files) {
  var fd = new FormData();
  fd.append('agent_id', String(cellId || ''));
  for (var i = 0; i < files.length; i++) {
    fd.append('file', files[i]);
  }
  var r = await fetch('/api/attachment/upload', { method: 'POST', body: fd });
  var res = r && typeof r.json === 'function' ? await r.json() : null;
  if (!res || !res.ok) {
    throw new Error((res && res.error) || 'Attachment upload failed.');
  }
  if (!Array.isArray(res.data)) {
    throw new Error('Attachment upload failed.');
  }
  return res.data
    .map(function(entry) { return entry && entry.path ? entry.path : ''; })
    .filter(Boolean);
}

function _terminalComposePersistFromDom(root) {
  const input = _terminalComposeTextarea(root);
  if (!input || !input.dataset || !input.dataset.cellId) return;
  _terminalComposeDrafts[input.dataset.cellId] = String(input.value || '');
}

function _captureTerminalWorkspaceState(root, cell) {
  if (typeof _captureSurfaceState !== 'function') return null;
  const snapshot = _captureSurfaceState(root);
  const active = document.activeElement;
  if (snapshot && snapshot.focus && active && active.dataset
      && active.dataset.cellId
      && cell && active.dataset.cellId !== String(cell.id || '')) {
    snapshot.focus = null;
  }
  return snapshot;
}

function _restoreTerminalWorkspaceState(root, snapshot, cell) {
  if (typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(root, snapshot);
  }
  const input = _terminalComposeTextarea(root);
  if (!input) return;
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  // Only re-assign value if the rendered textarea actually drifted from the
  // in-memory draft. Re-assigning a textarea's value resets its scrollTop
  // and would undo the cursor-into-view scroll that _restoreSurfaceState
  // just performed.
  if (cell && cellId === String(cell.id || '')
      && Object.prototype.hasOwnProperty.call(_terminalComposeDrafts, cellId)
      && input.value !== _terminalComposeDrafts[cellId]) {
    input.value = _terminalComposeDrafts[cellId];
  }
  _terminalComposeAutoResize(input);
  _terminalComposeSetButtonState(input);
}

function _renderTerminalCompose(root, cell) {
  if (!root) return;
  if (!cell || !cell.session_id) {
    if (root.innerHTML !== '') root.innerHTML = '';
    return;
  }
  const cellId = String(cell.id || '');
  const inputId = _terminalComposeInputId(cellId);
  const buttonId = _terminalComposeButtonId(cellId);
  const draft = Object.prototype.hasOwnProperty.call(_terminalComposeDrafts, cellId)
    ? _terminalComposeDrafts[cellId]
    : '';
  const error = Object.prototype.hasOwnProperty.call(_terminalComposeErrors, cellId)
    ? _terminalComposeErrors[cellId]
    : '';
  const disabled = !String(draft || '').trim();
  const placeholder = 'Send a message to ' + (cell.name || 'terminal') + '\u2026';

  // Idempotent path: if the form already exists for this cell, update only
  // the dynamic bits (placeholder, error, button disabled, draft value if it
  // drifted) without clobbering the textarea \u2014 clobbering destroys focus and
  // produces the LOOM:264 textbox-border flicker under multi-agent activity.
  const existingForm = root.querySelector ? root.querySelector('.terminal-compose') : null;
  const existingCellId = existingForm && existingForm.dataset
    ? String(existingForm.dataset.cellId || '')
    : '';
  if (existingForm && existingCellId === cellId) {
    const input = _terminalComposeTextarea(root);
    if (input) {
      if (input.placeholder !== placeholder) input.placeholder = placeholder;
      if (input.value !== draft) input.value = draft;
      _terminalComposeAutoResize(input);
      _terminalComposeSetButtonState(input);
    }
    const errorEl = existingForm.querySelector
      ? existingForm.querySelector('.terminal-compose-error')
      : null;
    if (errorEl && errorEl.textContent !== error) errorEl.textContent = error;
    const button = _terminalComposeButtonFor(input, cellId);
    if (button) {
      const shouldDisable = !!disabled;
      if (button.disabled !== shouldDisable) button.disabled = shouldDisable;
    }
    return;
  }

  root.innerHTML = ''
    + '<form class="terminal-compose" data-cell-id="' + esc(cellId) + '" onsubmit="return terminalComposeSubmit(event, \'' + esc(cellId) + '\')">'
    + '  <div class="terminal-compose-input-wrap">'
    + '  <textarea id="' + esc(inputId) + '" class="terminal-compose-input" rows="1"'
    + ' data-cell-id="' + esc(cellId) + '"'
    + ' placeholder="' + esc(placeholder) + '"'
    + ' oninput="terminalComposeInput(this)"'
    + ' onkeydown="terminalComposeKeydown(event, \'' + esc(cellId) + '\')"'
    + ' ondragenter="terminalComposeDragenter(event, \'' + esc(cellId) + '\')"'
    + ' ondragover="terminalComposeDragover(event, \'' + esc(cellId) + '\')"'
    + ' ondragleave="terminalComposeDragleave(event, \'' + esc(cellId) + '\')"'
    + ' ondrop="terminalComposeDrop(event, \'' + esc(cellId) + '\')">' + esc(draft) + '</textarea>'
    + '  <div class="terminal-compose-error" aria-live="polite">' + esc(error) + '</div>'
    + '  </div>'
    + '  <button id="' + esc(buttonId) + '" class="terminal-compose-submit" type="submit"'
    + (disabled ? ' disabled' : '')
    + ' title="Send message">Send</button>'
    + '</form>';
  const input = _terminalComposeTextarea(root);
  if (input) {
    input.value = draft;
    _terminalComposeAutoResize(input);
    _terminalComposeSetButtonState(input);
  }
}

function terminalComposeInput(el) {
  if (!el) return;
  const cellId = el.dataset ? (el.dataset.cellId || '') : '';
  if (cellId) _terminalComposeDrafts[cellId] = String(el.value || '');
  if (cellId && _terminalComposeErrors[cellId]) _terminalComposeSetError(el, '');
  _terminalComposeAutoResize(el);
  _terminalComposeSetButtonState(el);
}

function terminalComposeClear(cellId) {
  const id = String(cellId || '');
  const input = document.getElementById ? document.getElementById(_terminalComposeInputId(id)) : null;
  if (!input) return;
  input.value = '';
  if (id) _terminalComposeDrafts[id] = '';
  _terminalComposeAutoResize(input);
  _terminalComposeSetButtonState(input);
}

function _terminalComposeActiveSelection(input) {
  if (!input) return 0;
  if (input.selectionDirection === 'backward' && typeof input.selectionStart === 'number') {
    return input.selectionStart;
  }
  return typeof input.selectionEnd === 'number' ? input.selectionEnd : 0;
}

function _terminalComposeSelectionAnchor(input) {
  if (!input) return 0;
  if (input.selectionDirection === 'backward' && typeof input.selectionEnd === 'number') {
    return input.selectionEnd;
  }
  return typeof input.selectionStart === 'number' ? input.selectionStart : 0;
}

function _terminalComposeLineBoundary(value, caret, toEnd) {
  if (toEnd) {
    var lineEnd = value.indexOf('\n', caret);
    return lineEnd >= 0 ? lineEnd : value.length;
  }
  var lineStart = value.lastIndexOf('\n', caret > 0 ? caret - 1 : -1);
  return lineStart >= 0 ? lineStart + 1 : 0;
}

function _terminalComposeSetSelection(input, start, end, direction) {
  if (!input) return;
  var valueLength = typeof input.value === 'string' ? input.value.length : 0;
  start = Math.max(0, Math.min(valueLength, start));
  end = Math.max(0, Math.min(valueLength, end));
  if (typeof input.setSelectionRange === 'function') {
    input.setSelectionRange(start, end, direction || 'none');
  } else {
    input.selectionStart = start;
    input.selectionEnd = end;
    if ('selectionDirection' in input) input.selectionDirection = direction || 'none';
  }
}

function _terminalComposeMoveCaret(input, evt, toEnd, wholeBuffer) {
  if (!input || typeof input.value !== 'string') return false;
  var active = _terminalComposeActiveSelection(input);
  var anchor = _terminalComposeSelectionAnchor(input);
  var target = wholeBuffer
    ? (toEnd ? input.value.length : 0)
    : _terminalComposeLineBoundary(input.value, active, toEnd);
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt.shiftKey) {
    _terminalComposeSetSelection(
      input,
      Math.min(anchor, target),
      Math.max(anchor, target),
      target < anchor ? 'backward' : 'forward'
    );
  } else {
    _terminalComposeSetSelection(input, target, target, 'none');
  }
  return true;
}

function _terminalComposeScrollToBottom(cellId) {
  const id = String(cellId || '');
  for (const key in _embeddedTerminalSessions) {
    const entry = _embeddedTerminalSessions[key];
    const term = entry && entry.cellId === id ? entry.terminal : null;
    if (term && typeof term.scrollToBottom === 'function') term.scrollToBottom();
  }
}

function terminalComposeSubmit(evt, cellId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  const id = String(cellId || '');
  let input = null;
  if (evt && evt.currentTarget && evt.currentTarget.querySelector) {
    input = evt.currentTarget.querySelector('.terminal-compose-input');
  }
  if (!input && document.getElementById) {
    input = document.getElementById(_terminalComposeInputId(id));
  }
  if (!input) return false;
  const text = String(input.value || '');
  if (!text.trim()) {
    terminalComposeClear(id);
    return false;
  }
  send({ cmd: 'send_user_message', cell_id: id, text: text });
  _terminalComposeScrollToBottom(id);
  terminalComposeClear(id);
  return false;
}

function terminalComposeKeydown(evt, cellId) {
  if (!evt) return;
  if ((evt.key === 'Home' || evt.key === 'End') && !evt.altKey) {
    const input = evt.target && typeof evt.target.value === 'string'
      ? evt.target
      : (document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null);
    if (_terminalComposeMoveCaret(input, evt, evt.key === 'End', !!(evt.metaKey || evt.ctrlKey))) {
      return;
    }
  }
  if (evt.key === 'Escape') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    terminalComposeClear(cellId);
    return;
  }
  if (evt.key === 'Enter' && !evt.shiftKey) {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    terminalComposeSubmit(evt, cellId);
  }
}

function terminalComposeDragenter(evt, cellId) {
  if (!evt || !_terminalComposeHasDraggedFiles(evt.dataTransfer)) return;
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  const input = evt.currentTarget || (
    document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null
  );
  _terminalComposeSetDropTarget(input, true);
}

function terminalComposeDragover(evt, cellId) {
  if (!evt || !_terminalComposeHasDraggedFiles(evt.dataTransfer)) return;
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt.dataTransfer) evt.dataTransfer.dropEffect = 'copy';
  const input = evt.currentTarget || (
    document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null
  );
  _terminalComposeSetDropTarget(input, true);
}

function terminalComposeDragleave(evt, cellId) {
  if (!evt || !_terminalComposeHasDraggedFiles(evt.dataTransfer)) return;
  const input = evt.currentTarget || (
    document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null
  );
  _terminalComposeSetDropTarget(input, false);
}

async function terminalComposeDrop(evt, cellId) {
  if (!evt || !_terminalComposeHasDraggedFiles(evt.dataTransfer)) return false;
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
  const id = String(cellId || '');
  const input = evt.currentTarget || (
    document.getElementById ? document.getElementById(_terminalComposeInputId(id)) : null
  );
  _terminalComposeSetDropTarget(input, false);

  var files = _terminalComposeDroppedFiles(evt.dataTransfer);
  if (!files.length) {
    _terminalComposeSetError(input, 'Drop an image file to attach it.');
    return false;
  }
  var validation = terminalComposeValidateDroppedFiles(files);
  if (validation.errors.length) {
    _terminalComposeSetError(input, validation.errors[0]);
    return false;
  }
  if (!validation.accepted.length) {
    _terminalComposeSetError(input, 'Drop an image file to attach it.');
    return false;
  }

  try {
    _terminalComposeSetError(input, '');
    var paths = await _terminalComposeUploadAttachments(id, validation.accepted);
    if (!paths.length) {
      _terminalComposeSetError(input, 'Attachment upload failed.');
      return false;
    }
    _terminalComposeInsertPaths(input, paths);
  } catch (e) {
    _terminalComposeSetError(input, (e && e.message) || 'Attachment upload failed.');
  }
  return false;
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
  _embeddedTerminalSessions[sessionKey] = entry;
  if (entry.surface) {
    if (entry.surface.dataset) entry.surface.dataset.loomSessionKey = sessionKey;
    if (typeof entry.surface.setAttribute === 'function') {
      entry.surface.setAttribute('data-loom-session-key', sessionKey);
    }
  }
}

function _writeEmbeddedTerminalSessionRestartedSeparator(entry) {
  const term = entry && entry.terminal;
  if (!term) return;
  const line = '──── Loom session restarted ────';
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

function _sanitizeEmbeddedTerminalRekeySnapshot(data) {
  return String(data || '')
    // TUIs often start a fresh session with a hard reset and display-clear
    // sequence. During a same-cell re-key those clears would erase the
    // preserved scrollback we intentionally kept, so suppress them only for
    // the first snapshot after the session swap.
    .replace(/\x1bc/g, '')
    .replace(/\x1b\[[0-?]*[ -/]*J/g, '');
}

function _scheduleEmbeddedTerminalFit(entry) {
  entry = entry || _embeddedTerminalSessions[_embeddedTerminalSessionKey];
  if (!entry || !_isEmbeddedTerminalEntryActive(entry)
      || !entry.terminal || !entry.fit) return;
  requestAnimationFrame(function() {
    if (!_isEmbeddedTerminalEntryActive(entry) || !entry.terminal || !entry.fit) return;
    entry.fit.fit();
    if (entry.ws && entry.ws.readyState === WebSocket.OPEN) {
      entry.ws.send(JSON.stringify({
        type: 'resize',
        cols: entry.terminal.cols,
        rows: entry.terminal.rows,
      }));
      entry.ws.send(JSON.stringify({ type: 'focus' }));
    }
  });
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
  _attachEmbeddedTerminalDropHandlers(cell, surface, entry);
  try { entry.fit.fit(); } catch (e) { /* container not measurable yet */ }
  // Shift+Enter → send ESC+CR so TUIs like Claude Code treat it as a
  // soft newline instead of submitting. xterm.js default maps Shift+Enter
  // to plain `\r` (same as Enter), which submits prematurely.
  if (typeof entry.terminal.attachCustomKeyEventHandler === 'function') {
    entry.terminal.attachCustomKeyEventHandler(function(e) {
      if (e.type !== 'keydown') return true;
      if (e.key === 'Enter' && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (entry.ws && entry.ws.readyState === WebSocket.OPEN) {
          entry.ws.send(JSON.stringify({ type: 'input', data: '\x1b\r' }));
        }
        return false;
      }
      return true;
    });
  }
  entry.dataHandler = entry.terminal.onData(function(data) {
    if (entry.ws && entry.ws.readyState === WebSocket.OPEN) {
      entry.ws.send(JSON.stringify({ type: 'input', data: data }));
    }
  });
  entry.resizeObserver = new ResizeObserver(function() {
    _scheduleEmbeddedTerminalFit(entry);
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
      if (msg.data) entry.terminal.write(msg.data);
      if (_isEmbeddedTerminalEntryActive(entry)) {
        _scheduleEmbeddedTerminalFit(entry);
        focusEmbeddedTerminalWorkspace(false);
      }
    } else if (msg.type === 'output' && msg.data) {
      entry.terminal.write(msg.data);
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
    if (!cell) {
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
  if (surface && surface.dataset && surface.dataset.loomSessionKey) surface = null;
  if (!surface) {
    surface = document.createElement('div');
    if (stage && typeof stage.appendChild === 'function') stage.appendChild(surface);
  }
  surface.className = 'terminal-surface';
  if (surface.classList && typeof surface.classList.add === 'function') {
    surface.classList.add('terminal-surface');
  }
  if (surface.dataset) surface.dataset.loomSessionKey = sessionKey;
  if (typeof surface.setAttribute === 'function') {
    surface.setAttribute('data-loom-session-key', sessionKey);
  }
  return surface;
}

function _activateEmbeddedTerminalSurface(stage, sessionKey) {
  _clearEmbeddedTerminalStagePlaceholders(stage);
  const entry = _embeddedTerminalSessions[sessionKey] || null;
  for (const key in _embeddedTerminalSessions) {
    const candidate = _embeddedTerminalSessions[key];
    if (!candidate || !candidate.surface) continue;
    const active = key === sessionKey;
    if (active && stage && candidate.surface.parentNode !== stage
        && typeof stage.appendChild === 'function') {
      stage.appendChild(candidate.surface);
    }
    candidate.surface.hidden = !active;
    if (candidate.surface.style) candidate.surface.style.display = active ? '' : 'none';
  }
  _setActiveEmbeddedTerminalEntry(entry);
  if (entry) _scheduleEmbeddedTerminalFit(entry);
  return entry;
}

function _renderEmbeddedTerminalStagePlaceholder(stage, html) {
  if (!stage) return;
  const hasPlaceholder = !!(stage.children && Array.prototype.some.call(stage.children, function(child) {
    return !(child && child.classList && child.classList.contains('terminal-surface'));
  }));
  if (stage._loomLastHtml === html && hasPlaceholder) return;
  _clearEmbeddedTerminalStagePlaceholders(stage);
  const placeholder = document.createElement('div');
  placeholder.className = 'terminal-empty';
  if (placeholder.classList && typeof placeholder.classList.add === 'function') {
    placeholder.classList.add('terminal-empty');
  }
  placeholder.innerHTML = html;
  if (typeof stage.appendChild === 'function') stage.appendChild(placeholder);
  stage._loomLastHtml = html;
}

function renderTerminalWorkspace() {
  const root = document.getElementById('terminal-workspace');
  if (!root) return;
  _terminalComposePersistFromDom(root);
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
  const primaryAction = _terminalPrimaryAction(groupLabel, agentTarget);
  const relaunchAction = cell && !cell.session_id ? {
    label: 'Relaunch',
    onclick: 'relaunchAgent(\'' + esc(cell.id) + '\')',
  } : null;
  const topbarAction = cell ? (cell.session_id ? primaryAction : relaunchAction) : null;
  const showTabs = cells.length > 1;
  const displayPath = _terminalDisplayPath(cell);
  const dom = _ensureTerminalWorkspaceDom(root);
  const workspaceState = _captureTerminalWorkspaceState(root, cell);
  const title = cell && cell.name ? cell.name : 'Terminal';
  // Idempotent topbar/tabs: skip the innerHTML clobber when the rendered
  // HTML hasn't changed. Under multi-agent activity `renderTerminalWorkspace`
  // is called on every grid render (LOOM:264 firehose) — without this guard
  // we rewrite the topbar + tabs DOM dozens of times per second even though
  // nothing visible changed.
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
  if (dom.topbar._loomLastHtml !== topbarHtml) {
    dom.topbar.innerHTML = topbarHtml;
    dom.topbar._loomLastHtml = topbarHtml;
  }
  dom.tabs.classList.toggle('terminal-tabs-hidden', !showTabs);
  const tabsHtml = showTabs ? _renderTerminalTabs(cells, cell ? cell.id : '') : '';
  if (dom.tabs._loomLastHtml !== tabsHtml) {
    dom.tabs.innerHTML = tabsHtml;
    dom.tabs._loomLastHtml = tabsHtml;
  }

  if (!cell) {
    _renderTerminalCompose(dom.compose, null);
    const emptyHtml = ''
      + '<div class="terminal-empty">'
      + '  <div class="terminal-empty-title">Open a shell</div>'
      + '  <div class="terminal-empty-body">Start a standalone terminal for this workspace and Loom will drop you into it ready to type.</div>'
      + (primaryAction
        ? '  <button class="terminal-empty-btn" onclick="' + primaryAction.onclick + '">' + primaryAction.label + '</button>'
        : '')
      + '  <div class="terminal-empty-meta">The terminal will take focus automatically when it opens.</div>'
      + '</div>';
    if (dom.stage._loomLastHtml !== emptyHtml) {
      dom.stage.innerHTML = emptyHtml;
      dom.stage._loomLastHtml = emptyHtml;
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
    _renderTerminalCompose(dom.compose, null);
    const stoppedHtml = ''
      + '  <div class="terminal-empty-title">' + esc(cell.name) + ' is stopped</div>'
      + '  <div class="terminal-empty-body">Relaunch this session to put it back in the workspace and return keyboard focus to the shell.</div>'
      + '  <button class="terminal-empty-btn" onclick="relaunchAgent(\'' + esc(cell.id) + '\')">Relaunch</button>'
      + '  <div class="terminal-empty-meta">When it comes back, Loom will focus the terminal automatically.</div>';
    const stoppedEntry = _findEmbeddedTerminalEntryForCell(cell.id);
    if (stoppedEntry) {
      _activateEmbeddedTerminalSurface(dom.stage, stoppedEntry.sessionKey);
      dom.stage._loomLastHtml = null;
    } else {
      _activateEmbeddedTerminalSurface(dom.stage, sessionKey);
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
  _activateEmbeddedTerminalSurface(dom.stage, sessionKey);
  // The active branch attaches/toggles xterm surfaces directly on the stage
  // rather than rewriting `dom.stage.innerHTML`. Invalidate the empty/stopped
  // HTML cache so the next transition back to a no-cell / stopped-cell state
  // re-renders the empty placeholder.
  dom.stage._loomLastHtml = null;

  _renderTerminalCompose(dom.compose, cell);
  const statusText = (displayPath || 'No directory') + '  |  ' + _terminalStatusLabel(cell);
  if (dom.statusbar.textContent !== statusText) dom.statusbar.textContent = statusText;
  const statusTitle = cell.current_path || cell.directory || '';
  if (dom.statusbar.title !== statusTitle) dom.statusbar.title = statusTitle;
  _restoreTerminalWorkspaceState(root, workspaceState, cell);
}

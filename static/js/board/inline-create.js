/* Board module: inline create. */

function boardStartAddTask() {
  boardStartAddTaskForLane(_boardSelectedLane || _boardVisibleLanes()[0] || '');
}

function boardStartAddTaskForLane(lane) {
  var nextLane = lane || _boardSelectedLane || _boardVisibleLanes()[0] || '';
  var panel = document.getElementById('panel-board');
  var wideLayout = _boardWideLayoutActive(panel);
  if (_boardShowSchedules || nextLane !== _boardSelectedLane) {
    _boardPrepareViewChange(!wideLayout);
    _boardShowSchedules = false;
    var tabs = document.getElementById('board-lane-tabs');
    if (tabs) _boardScrollLeft = tabs.scrollLeft;
    _boardSelectedLane = nextLane;
    _boardSelectedTasks = {};
    _boardLastSelectedTask = '';
  }
  _boardAddingTask = true;
  _boardAddingTaskLane = _boardDefaultAddTaskLane();
  _boardAddTaskFocus = true;
  _boardFocusedTask = '';
  if (!_boardInlineDraftId) _boardInlineDraftId = _generateDraftId();
  renderBoard();
}

function boardCancelAddTask() {
  var el = document.getElementById('board-add-task-input');
  if (el) _boardAddingTaskDraft = el.value;
  // Keep open if there's a draft or attachments — user may be dragging files
  if (_boardAddingTaskDraft || _boardInlineAttachments.length) return;
  setTimeout(function() {
    // A re-render (mouse-move over cards that invalidates surfaces, a WS
    // delta, etc.) replaces the textarea via innerHTML, which fires blur
    // on the old node. The surface-state restore in renderBoard then
    // re-focuses the new textarea synchronously. If focus is on the
    // inline input at this point, the blur was render-driven — keep the
    // form open.
    var active = document.activeElement;
    if (active && active.id === 'board-add-task-input') return;
    _boardAddingTask = false;
    renderBoard();
  }, 150);
}

function boardClearAddTask() {
  // Clean up inline draft attachments
  if (_boardInlineDraftId && _boardInlineAttachments.length) {
    fetch('/api/upload/cleanup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: _boardInlineDraftId })
    });
  }
  _boardAddingTask = false;
  _boardAddingTaskDraft = '';
  _boardAddingTaskAgent = '';
  _boardAddingTaskLane = '';
  _boardInlineDraftId = '';
  _boardInlineAttachments = [];
  renderBoard();
}

/** Parse trailing %label tokens from inline task input. */

function _boardParseInlineLabels(text) {
  var tokens = text.trimEnd().split(/\s+/);
  var labels = [];
  while (tokens.length > 1 && tokens[tokens.length - 1].charAt(0) === '%') {
    labels.push(tokens.pop().slice(1).toLowerCase());
  }
  // Deduplicate
  var seen = {};
  var unique = [];
  for (var i = 0; i < labels.length; i++) {
    if (!seen[labels[i]] && labels[i]) { seen[labels[i]] = true; unique.push(labels[i]); }
  }
  return { title: tokens.join(' '), labels: unique };
}

function boardSubmitAddTask() {
  var el = document.getElementById('board-add-task-input');
  var val = el ? el.value.trim() : '';
  if (!val) return;
  var parsed = _boardParseInlineLabels(val);
  if (!parsed.title) return;
  var targetLane = _boardAddingTaskLane || _boardDefaultAddTaskLane();
  // Clear the live textarea immediately so any blur fired during the
  // upcoming innerHTML replacement (WebKit dispatches blur on focused
  // elements about to be detached) cannot reseed the draft from the
  // pre-submit text.
  if (el) el.value = '';
  _boardAddingTaskDraft = '';
  var msg = { cmd: 'board_add_task', task: parsed.title, group: _currentGroup(), lane: targetLane };
  if (_boardInlineDraftId) msg.id = _boardInlineDraftId;
  if (parsed.labels.length) msg.labels = parsed.labels;
  if (_boardAddingTaskAgent) msg.agent_id = _boardAddingTaskAgent;
  if (_boardInlineAttachments.length) msg.attachments = _boardInlineAttachments.slice();
  _boardAddingTaskAgent = '';
  _boardAddingTaskLane = '';
  _boardInlineDraftId = '';
  _boardInlineAttachments = [];
  // Keep the form open with a cleared textarea so the operator can
  // rapid-fire consecutive tasks. Re-focus the new textarea after the
  // upcoming render recreates it.
  _boardAddTaskFocus = true;
  send(msg);
  renderBoard();
}

/* ---- Inline add: drag-and-drop attachments -------------------------- */

function boardInlineDragOver(e) {
  e.preventDefault();
  e.currentTarget.classList.add('drag-over');
}

function boardInlineDragLeave(e) {
  e.currentTarget.classList.remove('drag-over');
}

function boardInlineDrop(e) {
  e.preventDefault();
  e.currentTarget.classList.remove('drag-over');
  if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
  if (!_boardInlineDraftId) _boardInlineDraftId = _generateDraftId();
  var files = e.dataTransfer.files;
  for (var i = 0; i < files.length; i++) {
    var file = files[i];
    if (!file.type.startsWith('image/')) continue;
    var fd = new FormData();
    fd.append('task_id', _boardInlineDraftId);
    fd.append('file', file);
    fetch('/api/upload', { method: 'POST', body: fd })
      .then(function(r) { return r.json(); })
      .then(function(res) {
        if (res.ok && res.data) {
          for (var j = 0; j < res.data.length; j++) {
            _boardInlineAttachments.push(res.data[j]);
          }
          renderBoard();
        }
      });
  }
}

function boardInlineRemoveAtt(idx) {
  var att = _boardInlineAttachments[idx];
  if (att && _boardInlineDraftId) {
    send({ cmd: 'remove_attachment', task_id: _boardInlineDraftId, filename: att.filename });
  }
  _boardInlineAttachments.splice(idx, 1);
  renderBoard();
}

var _boardLabelDropdownIdx = -1;

function boardAddTaskInput(el) {
  _boardAddingTaskDraft = el.value;
  boardAddTaskAutoResize(el);
  var text = el.value.substring(0, el.selectionStart);
  var match = text.match(/%([\w-]*)$/);
  var dropdown = document.getElementById('board-add-label-dropdown');
  if (!dropdown) return;
  if (!match) { dropdown.style.display = 'none'; _boardLabelDropdownIdx = -1; return; }
  var prefix = match[1].toLowerCase();
  var all = _getAllLabels();
  var filtered = [];
  for (var i = 0; i < all.length; i++) {
    if (all[i].toLowerCase().indexOf(prefix) >= 0) filtered.push(all[i]);
    if (filtered.length >= 8) break;
  }
  if (!filtered.length) { dropdown.style.display = 'none'; _boardLabelDropdownIdx = -1; return; }
  if (filtered.length === 1 && prefix === filtered[0].toLowerCase()) {
    dropdown.style.display = 'none'; _boardLabelDropdownIdx = -1;
    boardPickInlineLabel(filtered[0]); return;
  }
  _boardLabelDropdownIdx = -1;
  var html = '';
  for (var i = 0; i < filtered.length; i++) {
    html += '<div class="deps-option" onmousedown="boardPickInlineLabel(\'' + esc(filtered[i]) + '\')">' + esc(filtered[i]) + '</div>';
  }
  dropdown.innerHTML = html;
  dropdown.style.display = '';
}

function boardPickInlineLabel(label) {
  var el = document.getElementById('board-add-task-input');
  if (!el) return;
  var before = el.value.substring(0, el.selectionStart);
  var after = el.value.substring(el.selectionStart);
  el.value = before.replace(/%([\w-]*)$/, '%' + label + ' ') + after;
  document.getElementById('board-add-label-dropdown').style.display = 'none';
  _boardLabelDropdownIdx = -1;
  el.focus();
  el.selectionStart = el.selectionEnd = el.value.length;
}

function _boardAddTaskActiveSelection(el) {
  if (!el) return 0;
  if (el.selectionDirection === 'backward' && typeof el.selectionStart === 'number') {
    return el.selectionStart;
  }
  return typeof el.selectionEnd === 'number' ? el.selectionEnd : 0;
}

function _boardAddTaskSelectionAnchor(el) {
  if (!el) return 0;
  if (el.selectionDirection === 'backward' && typeof el.selectionEnd === 'number') {
    return el.selectionEnd;
  }
  return typeof el.selectionStart === 'number' ? el.selectionStart : 0;
}

function _boardAddTaskLineBoundary(value, caret, toEnd) {
  if (toEnd) {
    var lineEnd = value.indexOf('\n', caret);
    return lineEnd >= 0 ? lineEnd : value.length;
  }
  var lineStart = value.lastIndexOf('\n', Math.max(caret - 1, 0));
  return lineStart >= 0 ? lineStart + 1 : 0;
}

function _boardMoveAddTaskCaretToLineBoundary(el, e, toEnd) {
  if (!el || typeof el.value !== 'string') return false;
  var active = _boardAddTaskActiveSelection(el);
  var anchor = _boardAddTaskSelectionAnchor(el);
  var target = _boardAddTaskLineBoundary(el.value, active, toEnd);
  if (typeof e.preventDefault === 'function') e.preventDefault();
  if (e.shiftKey) {
    el.selectionStart = Math.min(anchor, target);
    el.selectionEnd = Math.max(anchor, target);
    if ('selectionDirection' in el) {
      el.selectionDirection = target < anchor ? 'backward' : 'forward';
    }
  } else {
    el.selectionStart = el.selectionEnd = target;
    if ('selectionDirection' in el) el.selectionDirection = 'none';
  }
  boardAddTaskInput(el);
  return true;
}

function boardAddTaskKeydown(e) {
  var input = e.target && typeof e.target.value === 'string'
    ? e.target
    : document.getElementById('board-add-task-input');
  var dropdown = document.getElementById('board-add-label-dropdown');
  var visible = dropdown && dropdown.style.display !== 'none';
  if (visible) {
    var opts = dropdown.querySelectorAll('.deps-option');
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _boardLabelDropdownIdx = Math.min(_boardLabelDropdownIdx + 1, opts.length - 1);
      _boardHighlightLabelOpt(opts);
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      _boardLabelDropdownIdx = Math.max(_boardLabelDropdownIdx - 1, 0);
      _boardHighlightLabelOpt(opts);
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      var idx = _boardLabelDropdownIdx >= 0 ? _boardLabelDropdownIdx : 0;
      if (opts[idx]) opts[idx].onmousedown();
      return;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      dropdown.style.display = 'none';
      _boardLabelDropdownIdx = -1;
      return;
    }
  }
  if ((e.key === 'Home' || e.key === 'End') && !e.altKey && !e.ctrlKey && !e.metaKey) {
    if (_boardMoveAddTaskCaretToLineBoundary(input, e, e.key === 'End')) return;
  }
  if (e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation();
    boardClearAddTask();
    return;
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    boardSubmitAddTask();
  }
}

function _boardHighlightLabelOpt(opts) {
  for (var i = 0; i < opts.length; i++) {
    opts[i].classList.toggle('active', i === _boardLabelDropdownIdx);
  }
}

function boardToggleAgentDropdown() {
  var wrap = document.getElementById('board-add-agent-wrap');
  var existing = wrap ? wrap.querySelector('.board-add-agent-list') : null;
  if (existing) { existing.remove(); return; }
  var grp = _currentGroup();
  var agents = [];
  if (state && state.groups && state.groups[grp]) {
    var aids = state.groups[grp];
    for (var i = 0; i < aids.length; i++) {
      var a = state.agents[aids[i]];
      if (_boardAgentIsLive(a)) agents.push(a);
    }
  }
  var listEl = document.createElement('div');
  listEl.className = 'board-add-agent-list';
  var noBtn = document.createElement('button');
  noBtn.className = 'board-add-menu-item' + (!_boardAddingTaskAgent ? ' selected' : '');
  noBtn.textContent = 'No agent';
  noBtn.onmousedown = function(e) { e.preventDefault(); };
  noBtn.onclick = function() { _boardAddingTaskAgent = ''; listEl.remove(); renderBoard(); };
  listEl.appendChild(noBtn);
  for (var j = 0; j < agents.length; j++) {
    (function(ag) {
      var btn = document.createElement('button');
      btn.className = 'board-add-menu-item' + (ag.id === _boardAddingTaskAgent ? ' selected' : '');
      btn.textContent = ag.name;
      btn.onmousedown = function(e) { e.preventDefault(); };
      btn.onclick = function() { _boardAddingTaskAgent = ag.id; listEl.remove(); renderBoard(); };
      listEl.appendChild(btn);
    })(agents[j]);
  }
  wrap.appendChild(listEl);
}

function boardToggleLaneDropdown() {
  var wrap = document.getElementById('board-add-lane-wrap');
  var existing = wrap ? wrap.querySelector('.board-add-agent-list') : null;
  if (existing) { existing.remove(); return; }
  var lanes = _boardAddTaskLaneOptions();
  var listEl = document.createElement('div');
  listEl.className = 'board-add-agent-list board-add-lane-list';
  for (var i = 0; i < lanes.length; i++) {
    (function(nextLane) {
      var btn = document.createElement('button');
      btn.className = 'board-add-menu-item' + (nextLane === _boardAddingTaskLane ? ' selected' : '');
      btn.textContent = nextLane;
      btn.onmousedown = function(e) { e.preventDefault(); };
      btn.onclick = function() {
        _boardAddingTaskLane = nextLane;
        listEl.remove();
        renderBoard();
      };
      listEl.appendChild(btn);
    })(lanes[i]);
  }
  wrap.appendChild(listEl);
}

function boardAddTaskAutoResize(el) {
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
}

/* ---- Card context menu ---------------------------------------------- */


/* ---- Multi-select / bulk operations --------------------------------- */

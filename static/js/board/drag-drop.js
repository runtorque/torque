/* Board module: drag drop. */

function _adjustCtxMenuOverflow() {
  var menu = document.getElementById('ctx-menu');
  if (!menu) return;
  requestAnimationFrame(function() {
    if (typeof positionContextMenuSurface === 'function') {
      positionContextMenuSurface(menu);
      return;
    }
    var rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth)
      menu.style.left = Math.max(8, window.innerWidth - rect.width - 8) + 'px';
    if (rect.bottom > window.innerHeight)
      menu.style.top = Math.max(8, window.innerHeight - rect.height - 8) + 'px';
  });
}

function _closeCtxMenu() {
  if (typeof closeContextMenu === 'function') {
    closeContextMenu({ restoreFocus: false });
    return;
  }
  var m = document.getElementById('ctx-menu');
  if (m) {
    m.classList.remove('open');
    m.setAttribute('aria-hidden', 'true');
  }
}

/* ---- Card drag and drop --------------------------------------------- */

function _boardCardDragSourceIsInteractive(target) {
  for (var node = target; node; node = node.parentElement || node.parentNode) {
    if (node.classList && node.classList.contains('board-card')) break;
    var tagName = String(node.tagName || '').toUpperCase();
    if (tagName === 'A' || tagName === 'BUTTON' || tagName === 'INPUT'
        || tagName === 'SELECT' || tagName === 'TEXTAREA') return true;
    if (node.getAttribute && (node.getAttribute('contenteditable') === 'true'
        || node.getAttribute('onclick'))) return true;
    if (node.classList && (
      node.classList.contains('board-card-collapse-btn')
      || node.classList.contains('board-card-menu-btn')
      || node.classList.contains('board-card-id-copy')
      || node.classList.contains('board-card-quick-controls')
      || node.classList.contains('board-card-quick-editor')
      || node.classList.contains('board-card-control-chip')
      || node.classList.contains('board-card-activity-clickable')
    )) return true;
  }
  return false;
}

function boardCardDragStart(e, id) {
  if (_boardCardDragSourceIsInteractive(e && e.target)) {
    if (e && typeof e.preventDefault === 'function') e.preventDefault();
    return;
  }
  _boardDragId = id;
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', id);
  }
  var card = e.target && e.target.closest ? e.target.closest('.board-card') : null;
  if (card) setTimeout(function() { card.classList.add('dragging'); }, 0);
}

function boardCardDragEnd(e) {
  _boardDragId = '';
  // Clean up all dragging/drop classes
  document.querySelectorAll('.board-card.dragging').forEach(function(el) {
    el.classList.remove('dragging');
  });
  document.querySelectorAll('.board-lane-drop-target.drop-target').forEach(function(el) {
    el.classList.remove('drop-target');
  });
  document.querySelectorAll('.board-card.drop-before,.board-card.drop-after').forEach(function(el) {
    el.classList.remove('drop-before', 'drop-after');
  });
}

function _boardClosestLaneDropTarget(node) {
  while (node) {
    if (node.classList && node.classList.contains('board-lane-drop-target')) return node;
    node = node.parentNode || null;
  }
  return null;
}

function _boardTaskLane(taskId) {
  var task = _boardTasks()[taskId];
  return task ? (task.lane || '') : '';
}

function _boardDropServerPosition(lane, targetId, placeAfter, movingWithinLane) {
  var tasks = _boardTasksInLane(lane);
  if (!movingWithinLane) {
    tasks = tasks.filter(function(task) { return task.id !== _boardDragId; });
  }
  var targetIdx = -1;
  for (var i = 0; i < tasks.length; i++) {
    if (tasks[i].id === targetId) {
      targetIdx = i;
      break;
    }
  }
  if (targetIdx < 0) return null;
  var pos = placeAfter ? targetIdx + 1 : targetIdx;
  var serverPos = tasks.length - pos - (movingWithinLane ? 1 : 0);
  return Math.max(0, serverPos);
}

function boardCardDragOver(e) {
  if (!_boardDragId) return;
  e.preventDefault();
  if (e.stopPropagation) e.stopPropagation();
  e.dataTransfer.dropEffect = 'move';

  // Show drop indicator
  var card = e.target.closest('.board-card');
  if (card && card.dataset.taskId !== _boardDragId) {
    var rect = card.getBoundingClientRect();
    var mid = rect.top + rect.height / 2;
    card.classList.remove('drop-before', 'drop-after');
    card.classList.add(e.clientY < mid ? 'drop-before' : 'drop-after');
  }
}

function boardCardDragLeave(e) {
  if (e.stopPropagation) e.stopPropagation();
  var card = e.target.closest('.board-card');
  if (card) card.classList.remove('drop-before', 'drop-after');
}

function boardCardDrop(e) {
  e.preventDefault();
  if (e.stopPropagation) e.stopPropagation();
  var card = e.target.closest('.board-card');
  if (!card || !_boardDragId) return;
  var targetId = card.dataset.taskId;
  if (targetId === _boardDragId) return;
  var sourceLane = _boardTaskLane(_boardDragId);
  var targetLane = _boardTaskLane(targetId) || _boardSelectedLane;
  if (!targetLane) return;

  var rect = card.getBoundingClientRect();
  var mid = rect.top + rect.height / 2;
  var placeAfter = e.clientY >= mid;
  if (sourceLane === targetLane) {
    if (_boardLaneSortMode(targetLane) !== 'manual') return;
    var serverPos = _boardDropServerPosition(targetLane, targetId, placeAfter, true);
    if (serverPos === null) return;
    send({ cmd: 'board_reorder_task', id: _boardDragId, position: serverPos });
  } else {
    var move = { cmd: 'board_move_task', id: _boardDragId, lane: targetLane };
    if (_boardLaneSortMode(targetLane) === 'manual') {
      var targetPos = _boardDropServerPosition(targetLane, targetId, placeAfter, false);
      if (targetPos !== null) move.position = targetPos;
    }
    send(move);
  }
  card.classList.remove('drop-before', 'drop-after');
}

// Drop on lane tab to move card to a different lane

function boardLaneTabDragOver(e) {
  if (!_boardDragId) return;
  e.preventDefault();
  if (e.stopPropagation) e.stopPropagation();
  e.dataTransfer.dropEffect = 'move';
  var target = _boardClosestLaneDropTarget(e.target);
  if (target) target.classList.add('drop-target');
}

function boardLaneTabDragLeave(e) {
  if (e.stopPropagation) e.stopPropagation();
  var target = _boardClosestLaneDropTarget(e.target);
  if (target) target.classList.remove('drop-target');
}

function boardLaneTabDrop(e) {
  e.preventDefault();
  if (e.stopPropagation) e.stopPropagation();
  var target = _boardClosestLaneDropTarget(e.target);
  if (!target || !_boardDragId) return;
  var lane = target.dataset.lane;
  if (lane && lane !== _boardTaskLane(_boardDragId)) {
    send({ cmd: 'board_move_task', id: _boardDragId, lane: lane });
  }
  target.classList.remove('drop-target');
}

/* ---- Search & filter ------------------------------------------------ */

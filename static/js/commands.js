/* Commands — actions sent to the daemon */

function focusAgent(id) { send({ cmd: 'focus_agent', id }); }

async function removeAgent(id) {
  const a = state.agents[id];
  if (a && await showConfirm(`Remove "${a.name}"?`)) {
    send({ cmd: 'remove_agent', id });
  }
}

function relaunchAgent(id) { send({ cmd: 'relaunch_agent', id }); }

function _nextName(prefix) {
  const existing = Object.values(state.agents)
    .map(a => a.name)
    .filter(n => n.startsWith(prefix + ' '));
  let i = 1;
  while (existing.includes(prefix + ' ' + i)) i++;
  return prefix + ' ' + i;
}
function quickAddAgent(group) {
  send({ cmd: 'add_agent', name: _nextName('Agent'), group });
}
function quickAddTerminal(group) {
  send({ cmd: 'add_terminal', name: _nextName('Terminal'), group });
}

async function restartDaemon() {
  if (await showConfirm('Restart Agent Matrix? Active cells will be marked as stopped.')) {
    send({ cmd: 'restart' });
  }
}

async function removeGroup(group) {
  const count = (state.groups[group] || []).length;
  const msg = count > 0
    ? `Remove group "${group}" and its ${count} cell(s)?`
    : `Remove empty group "${group}"?`;
  if (await showConfirm(msg)) send({ cmd: 'remove_group', group });
}

/* Drag and drop */
let _dragId = null;
let _dragType = null;

function setupDrag() {
  const main = document.getElementById('main');

  main.addEventListener('dragstart', (e) => {
    const el = e.target.closest('[data-drag-id]');
    if (!el) return;
    _dragId = el.dataset.dragId;
    _dragType = el.dataset.dragType;
    dragInProgress = true;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', _dragId);
    requestAnimationFrame(() => el.classList.add('dragging'));
  });

  main.addEventListener('dragend', () => {
    _dragId = null;
    _dragType = null;
    dragInProgress = false;
    _clearDropIndicators();
    render();
  });

  main.addEventListener('dragover', (e) => {
    if (!_dragId) return;
    const item = e.target.closest('[data-drag-id]');
    const container = e.target.closest('[data-drop-type]');

    _clearDropIndicators();

    if (item && item.dataset.dragType === _dragType && item.dataset.dragId !== _dragId) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      const rect = item.getBoundingClientRect();
      const isGrid = _dragType === 'agent';
      const pos = isGrid ? e.clientX : e.clientY;
      const mid = isGrid ? rect.left + rect.width / 2 : rect.top + rect.height / 2;
      item.classList.add(pos < mid ? 'drop-before' : 'drop-after');
    } else if (container && container.dataset.dropType === _dragType) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      container.classList.add('drop-target');
    }
  });

  main.addEventListener('drop', (e) => {
    if (!_dragId) return;
    e.preventDefault();
    const item = e.target.closest('[data-drag-id]');
    const container = e.target.closest('[data-drop-type]');
    let targetGroup = null;
    let beforeId = '';

    if (item && item.dataset.dragType === _dragType && item.dataset.dragId !== _dragId) {
      targetGroup = item.dataset.dragGroup;
      const rect = item.getBoundingClientRect();
      const isGrid = _dragType === 'agent';
      const pos = isGrid ? e.clientX : e.clientY;
      const mid = isGrid ? rect.left + rect.width / 2 : rect.top + rect.height / 2;
      if (pos < mid) {
        beforeId = item.dataset.dragId;
      } else {
        const next = _nextDragSibling(item);
        beforeId = next ? next.dataset.dragId : '';
      }
    } else if (container && container.dataset.dropType === _dragType) {
      targetGroup = container.dataset.dropGroup;
    }

    if (targetGroup) {
      send({ cmd: 'move_agent', id: _dragId, target_group: targetGroup, before: beforeId });
    }
    _clearDropIndicators();
  });
}

function _clearDropIndicators() {
  document.querySelectorAll('.dragging, .drop-before, .drop-after, .drop-target')
    .forEach(el => el.classList.remove('dragging', 'drop-before', 'drop-after', 'drop-target'));
}

function _nextDragSibling(el) {
  let next = el.nextElementSibling;
  while (next && !next.hasAttribute('data-drag-id')) next = next.nextElementSibling;
  return next;
}

/* Broadcast bar */
let broadcastGroup = null;

function openBroadcast(group) {
  broadcastGroup = group;
  document.getElementById('broadcast-target').textContent = '\u2192 ' + group;
  document.getElementById('broadcast').classList.add('visible');
  const inp = document.getElementById('broadcast-input');
  inp.value = '';
  inp.focus();
}
function closeBroadcast() {
  broadcastGroup = null;
  document.getElementById('broadcast').classList.remove('visible');
}
function sendBroadcast() {
  const text = document.getElementById('broadcast-input').value;
  if (text && broadcastGroup) {
    send({ cmd: 'broadcast_to_group', group: broadcastGroup, text: text + '\n' });
    document.getElementById('broadcast-input').value = '';
  }
}

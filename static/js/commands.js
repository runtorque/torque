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

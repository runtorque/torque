/* Modals — add group, add agent/terminal, confirm dialog, color picker */

let addCellMode = 'agent';
let _confirmResolve = null;
let _pendingModal = null;
let _selectedColor = '';

function closeModals() {
  document.querySelectorAll('.overlay').forEach(o => o.classList.remove('visible'));
  if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
}

/* -- Confirm dialog (replaces window.confirm for WKWebView) ----------- */
function showConfirm(message) {
  return new Promise((resolve) => {
    _confirmResolve = resolve;
    document.getElementById('confirm-message').textContent = message;
    document.getElementById('modal-confirm').classList.add('visible');
  });
}
function confirmYes() {
  document.getElementById('modal-confirm').classList.remove('visible');
  if (_confirmResolve) { _confirmResolve(true); _confirmResolve = null; }
}
function confirmNo() {
  document.getElementById('modal-confirm').classList.remove('visible');
  if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
}

/* -- Add Group -------------------------------------------------------- */
function openAddGroup() {
  document.getElementById('modal-group').classList.add('visible');
  const inp = document.getElementById('group-name-input');
  inp.value = '';
  inp.focus();
}
function submitGroup() {
  const name = document.getElementById('group-name-input').value.trim();
  if (name) { send({ cmd: 'add_group', group: name }); closeModals(); }
}

/* -- Add Agent / Terminal (shared modal) ------------------------------ */
function _openAddModal(mode, group) {
  _pendingModal = { mode, group };
  send({ cmd: 'get_config', group });
}

function _showAddModal(mode, group, config) {
  addCellMode = mode;
  _selectedColor = '';

  document.getElementById('modal-add-title').textContent =
    mode === 'agent' ? 'New Agent' : 'New Terminal';

  const cmdRow = document.getElementById('add-cmd-row');
  if (mode === 'terminal') cmdRow.classList.add('hidden');
  else cmdRow.classList.remove('hidden');

  /* group dropdown */
  const gsel = document.getElementById('add-group-select');
  gsel.innerHTML = '';
  for (const g of Object.keys(state.groups)) {
    const opt = document.createElement('option');
    opt.value = g; opt.textContent = g;
    if (g === group) opt.selected = true;
    gsel.appendChild(opt);
  }

  /* directory dropdown */
  const dsel = document.getElementById('add-dir-select');
  dsel.innerHTML = '';
  const optCur = document.createElement('option');
  optCur.value = config.current_path || '';
  optCur.textContent = 'Current session';
  dsel.appendChild(optCur);
  for (const c of (config.group_cells || [])) {
    const opt = document.createElement('option');
    opt.value = c.current_path;
    opt.textContent = 'Same as ' + c.name;
    dsel.appendChild(opt);
  }
  const optCustom = document.createElement('option');
  optCustom.value = '__custom__';
  optCustom.textContent = 'Custom\u2026';
  dsel.appendChild(optCustom);
  document.getElementById('add-dir-input').value = config.current_path || '';
  document.getElementById('add-dir-input').classList.add('hidden');

  /* profile dropdown */
  const psel = document.getElementById('add-profile-select');
  psel.innerHTML = '';
  for (const name of (config.profiles || ['Default'])) {
    const opt = document.createElement('option');
    opt.value = name; opt.textContent = name;
    if (name === config.current_profile) opt.selected = true;
    psel.appendChild(opt);
  }

  /* color swatches */
  const sw = document.getElementById('add-color-swatches');
  let sh = '';
  for (const c of TAB_COLORS) {
    sh += `<button class="swatch" data-color="${c.hex}" style="background:${c.hex}"
            onclick="selectColor('${c.hex}')" title="${c.name}"></button>`;
  }
  sh += `<button class="swatch swatch-none" data-color="" onclick="selectColor('')" title="None">\u2715</button>`;
  sw.innerHTML = sh;

  /* reset fields */
  document.getElementById('add-name-input').value = '';
  document.getElementById('add-cmd-input').value = '';
  document.getElementById('add-cmd-input').placeholder = 'claude';

  document.getElementById('modal-add').classList.add('visible');
  document.getElementById('add-name-input').focus();
}

function onDirChange() {
  const sel = document.getElementById('add-dir-select');
  const inp = document.getElementById('add-dir-input');
  if (sel.value === '__custom__') {
    inp.classList.remove('hidden');
    inp.focus();
  } else {
    inp.classList.add('hidden');
  }
}

function selectColor(hex) {
  _selectedColor = hex;
  document.querySelectorAll('#add-color-swatches .swatch').forEach(s => {
    s.classList.toggle('selected', (s.dataset.color || '') === hex);
  });
}

function openAddAgent(group)    { _openAddModal('agent', group); }
function openAddTerminal(group) { _openAddModal('terminal', group); }

function submitAdd() {
  const name    = document.getElementById('add-name-input').value.trim();
  const group   = document.getElementById('add-group-select').value;
  const command = document.getElementById('add-cmd-input').value.trim();
  const profile = document.getElementById('add-profile-select').value;

  const dirSel  = document.getElementById('add-dir-select');
  const directory = dirSel.value === '__custom__'
    ? document.getElementById('add-dir-input').value.trim()
    : dirSel.value;

  if (!name || !group) return;

  const msg = {
    cmd: addCellMode === 'agent' ? 'add_agent' : 'add_terminal',
    name, group, profile,
  };
  if (addCellMode === 'agent' && command) msg.command = command;
  if (directory) msg.directory = directory;
  if (_selectedColor) msg.tab_color = _selectedColor;

  send(msg);
  closeModals();
}

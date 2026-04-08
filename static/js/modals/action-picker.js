let _tplGroup = '';
let _tplName = '';
let _tplData = null;
let _tplTaskLane = '';    // lane for task mode

function openTaskFromAction(group, lane) {
  _tplGroup = group;
  _tplName = '';
  _tplData = null;
  _tplTaskLane = lane || '';
  send({ cmd: 'list_actions', group });
}

function _showActionList(msg) {
  const actions = msg.actions || [];
  const listEl = document.getElementById('tpl-list');
  const emptyEl = document.getElementById('tpl-empty');
  const listPane = document.getElementById('tpl-list-pane');
  const varsPane = document.getElementById('tpl-vars-pane');

  listPane.classList.remove('hidden');
  varsPane.classList.add('hidden');
  document.getElementById('tpl-back-btn').classList.add('hidden');
  document.getElementById('tpl-submit-btn').classList.add('hidden');
  document.getElementById('tpl-title').textContent = 'Task from Action';

  if (actions.length === 0) {
    listEl.innerHTML = '';
    emptyEl.classList.remove('hidden');
  } else {
    emptyEl.classList.add('hidden');
    let html = '';
    for (const t of actions) {
      const varCount = (t.vars || []).filter(v => v.name !== 'TASK').length;
      html += `<button class="tpl-item" onclick="_selectAction('${esc(t.name)}')">`;
      html += `<span class="tpl-item-name">${esc(t.name)}</span>`;
      if (t.description) html += `<span class="tpl-item-desc">${esc(t.description)}</span>`;
      if (varCount) html += `<span class="tpl-item-vars">${varCount} var${varCount > 1 ? 's' : ''}</span>`;
      html += `</button>`;
    }
    listEl.innerHTML = html;
  }
  document.getElementById('modal-action').classList.add('visible');
}

function _selectAction(name) {
  _tplName = name;
  send({ cmd: 'get_action', name, group: _tplGroup });
}

function _showActionVarForm(msg) {
  _tplData = msg;
  const vars = msg.vars || [];

  document.getElementById('tpl-list-pane').classList.add('hidden');
  document.getElementById('tpl-vars-pane').classList.remove('hidden');
  document.getElementById('tpl-back-btn').classList.remove('hidden');
  document.getElementById('tpl-submit-btn').classList.remove('hidden');
  document.getElementById('tpl-title').textContent = msg.name;

  const descEl = document.getElementById('tpl-description');
  descEl.textContent = (msg.action || {}).description || '';

  const fieldsEl = document.getElementById('tpl-var-fields');
  let html = '';

  for (const v of vars) {
    const req = v.required ? ' <span class="tpl-req">*</span>' : '';
    const label = v.description || v.name;
    html += `<label>${esc(label)}${req}</label>`;
    if (v.name === 'TASK') {
      html += `<textarea id="tpl-var-${esc(v.name)}" rows="3" placeholder="${esc(label)}">${esc(v.default || '')}</textarea>`;
    } else {
      html += `<input id="tpl-var-${esc(v.name)}" value="${esc(v.default || '')}" placeholder="${esc(label)}" autocomplete="off">`;
    }
  }
  fieldsEl.innerHTML = html;

  const first = fieldsEl.querySelector('textarea, input');
  if (first) setTimeout(() => first.focus(), 50);
}

function _tplBack() {
  send({ cmd: 'list_actions', group: _tplGroup });
}

function _tplSubmit() {
  if (!_tplData) return;
  const vars = {};
  for (const v of (_tplData.vars || [])) {
    const el = document.getElementById('tpl-var-' + v.name);
    if (el) vars[v.name] = el.value;
  }

  // Validate required fields
  for (const v of (_tplData.vars || [])) {
    if (v.required && !vars[v.name]) {
      const el = document.getElementById('tpl-var-' + v.name);
      if (el) { el.focus(); el.classList.add('input-error'); }
      return;
    }
  }

  // Open the task modal with this action pre-selected
  // TASK var goes into the task text field; other vars become action_vars
  closeModals();
  var actionVarValues = {};
  for (var vk in vars) {
    if (vk !== 'TASK') actionVarValues[vk] = vars[vk];
  }
  _taskOpenModal({
    editId: null,
    title: 'New Task',
    submitLabel: 'Create',
    task: vars['TASK'] || '',
    description: '',
    labels: [],
    dependsOn: [],
    attachments: [],
    originalAttachments: [],
    actionName: _tplName,
    agentTemplate: '',
    actionVars: actionVarValues,
    group: _tplGroup || _currentGroup(),
    lane: _tplTaskLane || '',
    scheduledInput: '',
    draftId: _generateDraftId(),
    selectTask: false,
  });
}

function _handleActionRendered(msg) {
  // "Task from Action" flow: pre-fill task modal with action selected
  _taskOpenModal({
    editId: null,
    title: 'New Task',
    submitLabel: 'Create',
    task: '',
    description: '',
    labels: msg.labels || [],
    dependsOn: [],
    attachments: [],
    originalAttachments: [],
    actionName: msg.name || _tplName || '',
    agentTemplate: '',
    actionVars: {},
    group: msg.group || _tplGroup || _currentGroup(),
    lane: _tplTaskLane || '',
    scheduledInput: '',
    draftId: _generateDraftId(),
    selectTask: false,
  });
}

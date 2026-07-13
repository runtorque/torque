/* Schedule editor modal. */

var _schedEditId = '';        // empty = create mode, set = edit mode
var _schedType = 'recurring'; // 'recurring' | 'oneshot'
var _schedLabels = [];
var _schedModalWaiting = false; // waiting for action list
var _schedDeferredAction = '';  // action to select once list loads (edit mode)
var _schedDeferredVars = {};    // vars to pre-fill once action is selected

function openScheduleModal(editId) {
  _schedEditId = editId || '';
  _schedLabels = [];
  _schedDeferredAction = '';
  _schedDeferredVars = {};

  // Populate group select
  var sel = document.getElementById('schedule-group-select');
  sel.innerHTML = '';
  var groups = state.groups || {};
  for (var g in groups) {
    var opt = document.createElement('option');
    opt.value = g;
    opt.textContent = g;
    sel.appendChild(opt);
  }

  // Populate action select (request from server)
  var actionSel = document.getElementById('schedule-action-select');
  actionSel.innerHTML = '<option value="">None</option>';
  var grp = sel.value || '';
  if (grp) {
    _schedModalWaiting = true;
    send({ cmd: 'list_actions', group: grp });
  }

  // Reset fields
  document.getElementById('schedule-name-input').value = '';
  document.getElementById('schedule-task-input').value = '';
  document.getElementById('schedule-desc-input').value = '';
  document.getElementById('schedule-cron-input').value = '';
  document.getElementById('schedule-at-input').value = '';
  document.getElementById('schedule-tz-input').value = '';
  document.getElementById('schedule-action-vars').innerHTML = '';
  document.getElementById('schedule-labels-chips').innerHTML = '';

  if (_schedEditId) {
    // Edit mode — populate from existing schedule
    var s = (state.schedules || {})[_schedEditId];
    if (!s) return;
    document.getElementById('schedule-modal-title').textContent = 'Edit Schedule';
    document.getElementById('schedule-submit-btn').textContent = 'Save';
    document.getElementById('schedule-name-input').value = s.name || '';
    document.getElementById('schedule-task-input').value = s.task_template || '';
    document.getElementById('schedule-desc-input').value = s.description || '';
    document.getElementById('schedule-tz-input').value = s.timezone || '';
    if (s.group) sel.value = s.group;

    if (s.cron_expr) {
      _schedType = 'recurring';
      document.getElementById('schedule-cron-input').value = s.cron_expr;
    } else {
      _schedType = 'oneshot';
      if (s.scheduled_at) {
        // Convert ISO to datetime-local format
        try {
          var d = new Date(s.scheduled_at);
          document.getElementById('schedule-at-input').value =
            d.toISOString().slice(0, 16);
        } catch(e) {}
      }
    }

    _schedLabels = (s.labels || []).slice();

    // Set action — deferred until action list loads
    if (s.action_name) {
      _schedDeferredAction = s.action_name;
      _schedDeferredVars = s.action_vars || {};
    }
  } else {
    document.getElementById('schedule-modal-title').textContent = 'New Schedule';
    document.getElementById('schedule-submit-btn').textContent = 'Create';
    _schedType = 'recurring';
  }

  scheduleSetType(_schedType);
  _schedRenderLabels();

  document.getElementById('modal-schedule').classList.add('visible');
  setTimeout(function() {
    document.getElementById('schedule-name-input').focus();
  }, 50);
}

function scheduleSetType(type) {
  _schedType = type;
  document.getElementById('schedule-type-recurring')
    .classList.toggle('active', type === 'recurring');
  document.getElementById('schedule-type-oneshot')
    .classList.toggle('active', type === 'oneshot');
  document.getElementById('schedule-cron-section')
    .style.display = type === 'recurring' ? '' : 'none';
  document.getElementById('schedule-at-section')
    .style.display = type === 'oneshot' ? '' : 'none';
}

function scheduleSetCron(expr) {
  document.getElementById('schedule-cron-input').value = expr;
}

var _schedActions = []; // cached action list for schedule modal

function scheduleActionChanged() {
  var sel = document.getElementById('schedule-action-select');
  var action = sel.value;
  var varsDiv = document.getElementById('schedule-action-vars');
  varsDiv.innerHTML = '';
  if (!action) return;

  // Look up variables from cached action list
  var act = null;
  for (var i = 0; i < _schedActions.length; i++) {
    if (_schedActions[i].name === action) { act = _schedActions[i]; break; }
  }
  if (act && act.vars) {
    var varNames = act.vars.filter(function(v) { return v.name !== 'TASK' && v.name !== 'torque'; })
      .map(function(v) { return v.name; });
    _schedRenderActionVars(varNames);
  }
}

function _schedRenderActionVars(vars) {
  var div = document.getElementById('schedule-action-vars');
  if (!div) return;
  div.innerHTML = '';
  if (!vars || !vars.length) return;

  var fs = document.createElement('fieldset');
  fs.className = 'action-vars-fieldset';
  var legend = document.createElement('legend');
  legend.textContent = 'Action variables';
  fs.appendChild(legend);

  for (var i = 0; i < vars.length; i++) {
    var v = vars[i];
    if (v === 'TASK' || v === 'torque') continue;
    var label = document.createElement('label');
    label.textContent = v;
    var ta = document.createElement('textarea');
    ta.className = 'action-var-input';
    ta.rows = 1;
    ta.dataset.var = v;
    ta.oninput = function() { taskAutoResize(this); };
    fs.appendChild(label);
    fs.appendChild(ta);
  }
  div.appendChild(fs);
}

function scheduleLabelsKeydown(e) {
  if (e.key !== 'Enter') return;
  e.preventDefault();
  var inp = document.getElementById('schedule-labels-input');
  var val = inp.value.trim();
  if (val && _schedLabels.indexOf(val) === -1) {
    _schedLabels.push(val);
    _schedRenderLabels();
  }
  inp.value = '';
}

function _schedRenderLabels() {
  var div = document.getElementById('schedule-labels-chips');
  div.innerHTML = '';
  for (var i = 0; i < _schedLabels.length; i++) {
    var lbl = _schedLabels[i];
    var chip = document.createElement('span');
    chip.className = 'label-chip';
    chip.textContent = lbl;
    chip.dataset.index = i;
    chip.onclick = function() {
      _schedLabels.splice(parseInt(this.dataset.index), 1);
      _schedRenderLabels();
    };
    div.appendChild(chip);
  }
}

function _handleScheduleActionList(msg) {
  _schedModalWaiting = false;
  var actions = msg.actions || [];
  _schedActions = actions;
  var sel = document.getElementById('schedule-action-select');
  if (!sel) return;
  var prev = sel.value;
  sel.innerHTML = '<option value="">None</option>';
  for (var i = 0; i < actions.length; i++) {
    var a = actions[i];
    var opt = document.createElement('option');
    opt.value = a.name;
    opt.textContent = a.name;
    sel.appendChild(opt);
  }
  if (prev) sel.value = prev;

  // Apply deferred action selection (edit mode)
  if (_schedDeferredAction) {
    sel.value = _schedDeferredAction;
    _schedDeferredAction = '';
    scheduleActionChanged();
    // Pre-fill vars
    if (_schedDeferredVars && Object.keys(_schedDeferredVars).length) {
      var inputs = document.getElementById('schedule-action-vars')
        .querySelectorAll('textarea');
      for (var i = 0; i < inputs.length; i++) {
        var key = inputs[i].dataset.var;
        if (key && _schedDeferredVars[key]) {
          inputs[i].value = _schedDeferredVars[key];
        }
      }
      _schedDeferredVars = {};
    }
  }
}

function submitSchedule() {
  var name = document.getElementById('schedule-name-input').value.trim();
  var group = document.getElementById('schedule-group-select').value;
  if (!name) return;
  if (!group) return;

  var payload = {
    name: name,
    group: group,
    task_template: document.getElementById('schedule-task-input').value.trim(),
    description: document.getElementById('schedule-desc-input').value.trim(),
    timezone: document.getElementById('schedule-tz-input').value.trim(),
    labels: _schedLabels.slice(),
  };

  if (_schedType === 'recurring') {
    var cron = document.getElementById('schedule-cron-input').value.trim();
    if (!cron) return;
    payload.cron_expr = cron;
  } else {
    var at = document.getElementById('schedule-at-input').value;
    if (!at) return;
    payload.scheduled_at = new Date(at).toISOString();
  }

  var action = document.getElementById('schedule-action-select').value;
  if (action) {
    payload.action_name = action;
    var vars = {};
    var inputs = document.getElementById('schedule-action-vars')
      .querySelectorAll('textarea');
    for (var i = 0; i < inputs.length; i++) {
      var key = inputs[i].dataset.var;
      var val = inputs[i].value.trim();
      if (key && val) vars[key] = val;
    }
    if (Object.keys(vars).length) payload.action_vars = vars;
  }

  if (_schedEditId) {
    payload.cmd = 'schedule_update';
    payload.id = _schedEditId;
  } else {
    payload.cmd = 'schedule_create';
  }

  send(payload);
  closeModals();
}

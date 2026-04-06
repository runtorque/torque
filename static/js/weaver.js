/* Weaver panel — Journal / Settings tabs */

var _weaverTab = 'journal';  // 'journal' | 'settings'
var _weaverCustomInstrDirty = false;
var _weaverCustomInstrDraft = '';

function renderWeaverPanel() {
  var el = document.getElementById('panel-weaver');
  if (!el) return;

  // Find the first group with a weaver
  var group = _weaverFindGroup();
  var ws = _weaverGetSettings(group);
  var weaver = group ? _weaverGetAgent(group) : null;

  var html = '<div class="weaver-panel">';

  // Header
  html += '<div class="weaver-header">';
  html += '<span class="weaver-title">Weaver';
  if (group) html += ' — ' + _esc(group);
  html += '</span>';
  // Pause/Resume toggle
  if (group) {
    var paused = ws && ws.paused;
    html += '<button class="weaver-pause-btn' + (paused ? ' paused' : '') + '" '
         + 'onclick="weaverTogglePause()">'
         + (paused ? '&#x25B6; Resume' : '&#x23F8; Pause')
         + '</button>';
  }
  html += '</div>';

  // Tabs
  html += '<div class="weaver-tabs">';
  html += '<button class="weaver-tab' + (_weaverTab === 'journal' ? ' active' : '') + '" '
       + 'onclick="weaverSwitchTab(\'journal\')">Journal</button>';
  html += '<button class="weaver-tab' + (_weaverTab === 'settings' ? ' active' : '') + '" '
       + 'onclick="weaverSwitchTab(\'settings\')">Settings</button>';
  html += '</div>';

  // Tab content
  html += '<div class="weaver-content">';
  if (_weaverTab === 'journal') {
    html += _weaverRenderJournal(group);
  } else {
    html += _weaverRenderSettings(group, ws, weaver);
  }
  html += '</div>';
  html += '</div>';
  el.innerHTML = html;
}

function weaverSwitchTab(tab) {
  _weaverTab = tab;
  renderWeaverPanel();
}

function weaverTogglePause() {
  var group = _weaverFindGroup();
  if (!group) return;
  var ws = _weaverGetSettings(group);
  var cmd = (ws && ws.paused) ? 'weaver_resume' : 'weaver_pause';
  send({ cmd: cmd, group: group });
}

// -- Journal tab -----------------------------------------------------------

function _weaverRenderJournal(group) {
  if (!group) {
    return '<div class="weaver-empty">No weaver configured for any group.</div>';
  }

  // Journal entries come from state.weaver_journal (populated by delta ops)
  var entries = (state.weaver_journal && state.weaver_journal[group]) || [];
  if (!entries.length) {
    return '<div class="weaver-empty">No journal entries yet.</div>';
  }

  var html = '<div class="weaver-journal">';
  // Show newest first
  for (var i = entries.length - 1; i >= 0; i--) {
    var e = entries[i];
    var typeClass = 'weaver-badge-' + (e.type || 'observation');
    var ago = _weaverTimeAgo(e.timestamp);
    html += '<div class="weaver-entry">';
    html += '<div class="weaver-entry-header">';
    html += '<span class="weaver-badge ' + typeClass + '">' + _esc(e.type || '?') + '</span>';
    html += '<span class="weaver-entry-time">' + ago + '</span>';
    html += '</div>';
    html += '<div class="weaver-entry-text">' + _esc(e.entry || '') + '</div>';
    html += '</div>';
  }
  html += '</div>';
  return html;
}

// -- Settings tab ----------------------------------------------------------

function _weaverRenderSettings(group, ws, weaver) {
  var html = '';

  // Agent section
  html += '<div class="weaver-section">';
  html += '<div class="weaver-section-title">Agent</div>';
  if (weaver) {
    html += '<div class="weaver-agent-row">';
    html += '<span class="weaver-agent-name">' + _esc(weaver.name) + '</span>';
    html += '<span class="weaver-agent-status status-' + (weaver.status || 'stopped') + '">'
         + _esc(weaver.status || 'stopped') + '</span>';
    html += '</div>';
  } else {
    html += '<div class="weaver-empty">No weaver designated.</div>';
  }
  html += '</div>';

  // Custom Instructions section
  html += '<div class="weaver-section">';
  html += '<div class="weaver-section-title">Custom Instructions</div>';
  var ci = _weaverCustomInstrDirty
    ? _weaverCustomInstrDraft
    : (ws ? ws.custom_instructions || '' : '');
  html += '<textarea class="weaver-instructions" '
       + 'placeholder="Instructions appended to the weaver system prompt..." '
       + 'oninput="weaverInstrInput(this)">' + _esc(ci) + '</textarea>';
  if (_weaverCustomInstrDirty) {
    html += '<button class="weaver-save-btn" onclick="weaverSaveInstructions()">Save</button>';
  }
  html += '</div>';

  // Notifications section
  html += '<div class="weaver-section">';
  html += '<div class="weaver-section-title">Notifications</div>';

  var pushInt = (ws && ws.push_interval) || 60;
  var maxInt = (ws && ws.max_interval) || 300;
  html += '<div class="weaver-field"><label>Push interval</label>';
  html += '<select onchange="weaverUpdateSetting(\'push_interval\', +this.value)">';
  [10, 30, 60, 120, 300].forEach(function(v) {
    var sel = v === pushInt ? ' selected' : '';
    html += '<option value="' + v + '"' + sel + '>' + v + 's</option>';
  });
  html += '</select></div>';

  html += '<div class="weaver-field"><label>Max interval</label>';
  html += '<select onchange="weaverUpdateSetting(\'max_interval\', +this.value)">';
  [60, 120, 300, 600].forEach(function(v) {
    var sel = v === maxInt ? ' selected' : '';
    html += '<option value="' + v + '"' + sel + '>' + v + 's</option>';
  });
  html += '</select></div>';

  // Event checkboxes
  var mandatory = ['task_completed', 'agent_error', 'agent_reply',
                   'agent_blocked', 'ask_created'];
  var optional = ['agent_started', 'task_dispatched', 'task_derived',
                  'agent_progress'];
  var enabled = (ws && ws.enabled_events) || [];

  html += '<div class="weaver-events-list">';
  mandatory.forEach(function(evt) {
    html += '<label class="weaver-event-check mandatory">'
         + '<input type="checkbox" checked disabled>'
         + '<span>' + evt + ' (mandatory)</span></label>';
  });
  optional.forEach(function(evt) {
    var checked = enabled.indexOf(evt) >= 0 ? ' checked' : '';
    html += '<label class="weaver-event-check">'
         + '<input type="checkbox"' + checked
         + ' onchange="weaverToggleEvent(\'' + evt + '\', this.checked)">'
         + '<span>' + evt + '</span></label>';
  });
  html += '</div>';
  html += '</div>';

  return html;
}

// -- Event handlers --------------------------------------------------------

function weaverInstrInput(textarea) {
  _weaverCustomInstrDirty = true;
  _weaverCustomInstrDraft = textarea.value;
  // Show save button (re-render just the settings section would be heavy;
  // instead just toggle the button visibility)
  var btn = textarea.parentElement.querySelector('.weaver-save-btn');
  if (!btn) {
    var b = document.createElement('button');
    b.className = 'weaver-save-btn';
    b.textContent = 'Save';
    b.onclick = weaverSaveInstructions;
    textarea.parentElement.appendChild(b);
  }
}

function weaverSaveInstructions() {
  var group = _weaverFindGroup();
  if (!group) return;
  send({
    cmd: 'weaver_update_settings',
    group: group,
    custom_instructions: _weaverCustomInstrDraft,
  });
  _weaverCustomInstrDirty = false;
  _weaverCustomInstrDraft = '';
}

function weaverUpdateSetting(key, value) {
  var group = _weaverFindGroup();
  if (!group) return;
  var payload = { cmd: 'weaver_update_settings', group: group };
  payload[key] = value;
  send(payload);
}

function weaverToggleEvent(evt, enabled) {
  var group = _weaverFindGroup();
  if (!group) return;
  var ws = _weaverGetSettings(group);
  var current = (ws && ws.enabled_events) ? ws.enabled_events.slice() : [];
  if (enabled && current.indexOf(evt) < 0) {
    current.push(evt);
  } else if (!enabled) {
    current = current.filter(function(e) { return e !== evt; });
  }
  send({
    cmd: 'weaver_update_settings',
    group: group,
    enabled_events: current,
  });
}

// -- Helpers ---------------------------------------------------------------

function _weaverFindGroup() {
  if (!state.group_settings) return '';
  for (var gn in state.group_settings) {
    if (state.group_settings[gn].weaver_agent_id) return gn;
  }
  return '';
}

function _weaverGetSettings(group) {
  if (!group || !state.weaver_settings) return null;
  return state.weaver_settings[group] || null;
}

function _weaverGetAgent(group) {
  if (!group || !state.group_settings) return null;
  var gs = state.group_settings[group];
  if (!gs || !gs.weaver_agent_id) return null;
  return state.agents ? state.agents[gs.weaver_agent_id] : null;
}

function _weaverTimeAgo(ts) {
  if (!ts) return '';
  var diff = (Date.now() / 1000) - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function _esc(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;')
          .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

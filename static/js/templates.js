/* ------------------------------------------------------------------ */
/* Agent templates editor + history                                    */
/* ------------------------------------------------------------------ */

var _agentsPanelView = 'templates';  // 'templates' | 'history'

var _agentTplList = [];
var _agentTplSelected = '';
var _agentTplData = null;
var _agentTplDirty = false;
var _agentTplNew = false;
var _agentTplScope = 'project';

function _agentTplKey(t) {
  return (t.global ? 'user:' : 'project:') + t.name;
}

function _agentTplSelectedName() {
  var idx = _agentTplSelected.indexOf(':');
  return idx >= 0 ? _agentTplSelected.slice(idx + 1) : _agentTplSelected;
}

function agentTemplateEditorLoad() {
  send({ cmd: 'get_config', group: _currentGroup() });
  send({ cmd: 'list_templates', group: _currentGroup() });
}

function agentTemplateReceiveList(msg) {
  _agentTplList = msg.templates || [];
  if (msg.saved) {
    var match = _agentTplList.find(function(t) { return t.name === msg.saved; });
    if (match) _agentTplSelected = _agentTplKey(match);
  }
  if (msg.deleted && _agentTplSelectedName() === msg.deleted) {
    _agentTplSelected = '';
    _agentTplData = null;
  }
  renderAgentTemplatesPanel();
  if (_agentTplSelected && !_agentTplNew) {
    send({
      cmd: 'get_template',
      name: _agentTplSelectedName(),
      group: _currentGroup(),
      scope: _agentTplScope,
    });
  }
}

function agentTemplateReceiveDetail(msg) {
  if (msg.name !== _agentTplSelectedName()) return;
  _agentTplData = msg.template || {};
  _agentTplDirty = false;
  _agentTplNew = false;
  renderAgentTemplatesEditor();
}

function agentsPanelSwitchView(view) {
  _agentsPanelView = view;
  if (view === 'history') {
    agentHistoryLoad();
  } else {
    renderAgentTemplatesPanel();
  }
}

function renderAgentTemplatesPanel() {
  var panel = document.getElementById('panel-templates');
  if (!panel) return;

  var html = '';
  html += '<div class="tpled-header">';

  // View toggle (Templates | History)
  html += '<div class="tpled-view-toggle" style="margin-left:0;margin-right:auto">';
  html += '<button class="tpled-view-btn' + (_agentsPanelView === 'templates' ? ' active' : '') + '" onclick="agentsPanelSwitchView(\'templates\')">Templates</button>';
  html += '<button class="tpled-view-btn' + (_agentsPanelView === 'history' ? ' active' : '') + '" onclick="agentsPanelSwitchView(\'history\')">History</button>';
  html += '</div>';

  if (_agentsPanelView === 'history') {
    html += '</div>';
    html += '<div class="agent-history-container" id="agent-history-container"></div>';
    panel.innerHTML = html;
    renderAgentHistoryView();
    return;
  }

  // Templates view (existing)
  html += '<select class="tpled-select" id="agent-tpl-select" onchange="agentTemplateSelect(this.value)">';
  html += '<option value="">Select\u2026</option>';
  var project = _agentTplList.filter(function(t) { return !t.global; });
  var user = _agentTplList.filter(function(t) { return t.global; });
  if (project.length) {
    html += '<optgroup label="Project">';
    for (var i = 0; i < project.length; i++) {
      var key = _agentTplKey(project[i]);
      html += '<option value="' + esc(key) + '"' + (key === _agentTplSelected ? ' selected' : '') + '>'
        + esc(project[i].display_name || project[i].name) + '</option>';
    }
    html += '</optgroup>';
  }
  if (user.length) {
    html += '<optgroup label="User">';
    for (var j = 0; j < user.length; j++) {
      var ukey = _agentTplKey(user[j]);
      var suffix = user[j].shadowed ? ' (overridden)' : '';
      html += '<option value="' + esc(ukey) + '"' + (ukey === _agentTplSelected ? ' selected' : '') + '>'
        + esc(user[j].display_name || user[j].name) + suffix + '</option>';
    }
    html += '</optgroup>';
  }
  html += '</select>';
  html += '<button class="tpled-new-btn" onclick="agentTemplateNew()" title="New template">+</button>';
  html += '<button class="tpled-new-btn" onclick="agentTemplateEditorLoad()" title="Refresh">&#x21BB;</button>';
  html += '</div>';
  html += '<div class="tpled-editor" id="agent-tpl-editor"></div>';

  panel.innerHTML = html;
  renderAgentTemplatesEditor();
}

function agentTemplateSelect(key) {
  if (!key) {
    _agentTplSelected = '';
    _agentTplData = null;
    _agentTplNew = false;
    _agentTplDirty = false;
    renderAgentTemplatesEditor();
    return;
  }
  _agentTplSelected = key;
  var parts = key.split(':');
  _agentTplScope = parts[0] === 'user' ? 'user' : 'project';
  _agentTplNew = false;
  _agentTplData = null;
  _agentTplDirty = false;
  renderAgentTemplatesPanel();
  send({
    cmd: 'get_template',
    name: parts.slice(1).join(':'),
    group: _currentGroup(),
    scope: _agentTplScope,
  });
}

function agentTemplateNew() {
  _agentTplSelected = '';
  _agentTplNew = true;
  _agentTplDirty = true;
  _agentTplScope = 'project';
  _agentTplData = { name: '', display_name: '', env_vars: {}, terminals: [] };
  renderAgentTemplatesPanel();
  var inp = document.getElementById('agent-template-name');
  if (inp) inp.focus();
}

function renderAgentTemplatesEditor() {
  var el = document.getElementById('agent-tpl-editor');
  if (!el) return;
  if (!_agentTplData && !_agentTplNew) {
    if (_agentTplList.length === 0) {
      el.innerHTML = '<div class="tpled-empty">No agent templates found.<br>Click <b>+</b> to create one,<br>or add <code>.yaml</code> files to <code>.loom/agents/</code>.</div>';
    } else {
      el.innerHTML = '<div class="tpled-empty">Pick a template from the dropdown above.</div>';
    }
    return;
  }

  var d = _agentTplData || {};
  var html = '<div class="tpled-form">';
  html += '<label>Name <span class="label-req">*</span></label>';
  html += '<input id="agent-template-name" value="' + esc(d.name || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off">';
  html += '<label>Scope</label>';
  html += '<select id="agent-template-scope" onchange="agentTemplateMarkDirty()">';
  html += '<option value="project"' + (_agentTplScope === 'project' ? ' selected' : '') + '>Project (.loom/agents/)</option>';
  html += '<option value="user"' + (_agentTplScope === 'user' ? ' selected' : '') + '>User (~/.loom/agents/)</option>';
  html += '</select>';
  html += '<label>Display name</label>';
  html += '<input id="agent-template-display" value="' + esc(d.display_name || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off">';
  html += '<label>Description</label>';
  html += '<input id="agent-template-desc" value="' + esc(d.description || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off">';

  html += '<details class="tpled-section" open><summary>Provider</summary>';
  html += '<label>Agent CLI</label><select id="agent-template-provider" onchange="agentTemplateProviderChanged();agentTemplateMarkDirty()"></select>';
  html += '<label>Command override</label><input id="agent-template-command" value="' + esc(d.command || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off" placeholder="Leave blank to use provider default">';
  html += '<label>Model</label><input id="agent-template-model" value="' + esc(d.model || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off">';
  html += '<label>Permissions</label><input id="agent-template-permissions" value="' + esc(d.permissions || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off" placeholder="Claude: skip or allowlist">';
  html += '<label>Max turns</label><input id="agent-template-max-turns" type="number" min="0" value="' + esc(d.max_turns || '') + '" onchange="agentTemplateMarkDirty()">';
  html += '</details>';

  html += '<details class="tpled-section"' + (d.system_prompt || d.initial_prompt ? ' open' : '') + '><summary>Behavior</summary>';
  html += '<label>System prompt</label><textarea id="agent-template-system-prompt" rows="4" oninput="_tplAutoResize(this)" onchange="agentTemplateMarkDirty()">' + esc(d.system_prompt || '') + '</textarea>';
  html += '<label>Initial prompt</label><textarea id="agent-template-initial-prompt" rows="3" oninput="_tplAutoResize(this)" onchange="agentTemplateMarkDirty()">' + esc(d.initial_prompt || '') + '</textarea>';
  html += '<label class="gs-checkbox"><input id="agent-template-session-resume" type="checkbox"' + (d.session_resume !== false ? ' checked' : '') + ' onchange="agentTemplateMarkDirty()"> Resume session on relaunch</label>';
  html += '<label>Idle timeout <span class="label-hint">minutes</span></label><input id="agent-template-idle-timeout" type="number" min="0" value="' + esc(d.idle_timeout != null ? d.idle_timeout : 5) + '" onchange="agentTemplateMarkDirty()">';
  html += '</details>';

  html += '<details class="tpled-section"' + (d.tab_color || d.icon ? ' open' : '') + '><summary>Visual</summary>';
  html += '<label>Tab color</label><input id="agent-template-color" value="' + esc(d.tab_color || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off" placeholder="#hex">';
  html += '<label>Icon</label><input id="agent-template-icon" value="' + esc(d.icon || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off" placeholder="emoji or symbol">';
  html += '</details>';

  html += '<details class="tpled-section"' + (d.worktree || d.worktree_base_branch ? ' open' : '') + '><summary>Worktree</summary>';
  html += '<label class="gs-checkbox"><input id="agent-template-worktree" type="checkbox"' + (d.worktree ? ' checked' : '') + ' onchange="agentTemplateMarkDirty()"> Enable git worktree</label>';
  html += '<label>Base branch</label><input id="agent-template-worktree-base" value="' + esc(d.worktree_base_branch || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off">';
  html += '<label class="gs-checkbox"><input id="agent-template-auto-checkpoint" type="checkbox"' + (d.worktree_auto_checkpoint ? ' checked' : '') + ' onchange="agentTemplateMarkDirty()"> Auto-checkpoint on stop</label>';
  html += '<label class="gs-checkbox"><input id="agent-template-merge-squash" type="checkbox"' + (d.worktree_merge_squash !== false ? ' checked' : '') + ' onchange="agentTemplateMarkDirty()"> Squash on merge</label>';
  html += '</details>';

  html += '<details class="tpled-section"' + ((_envToText(d.env_vars) || (d.terminals || []).length) ? ' open' : '') + '><summary>Environment & terminals</summary>';
  html += '<label>Environment <span class="label-hint">KEY=VALUE per line</span></label>';
  html += '<textarea id="agent-template-env" rows="3" oninput="_tplAutoResize(this)" onchange="agentTemplateMarkDirty()">' + esc(_envToText(d.env_vars || {})) + '</textarea>';
  html += '<label>Child terminals</label>';
  html += '<div id="agent-template-terminals">';
  for (var i = 0; i < (d.terminals || []).length; i++) {
    html += _agentTemplateTerminalRow(i, d.terminals[i]);
  }
  html += '</div>';
  html += '<button class="tpled-transition-add" onclick="agentTemplateAddTerminal()">+ Add terminal</button>';
  html += '</details>';

  html += '<div class="tpled-actions">';
  html += '<button class="btn-primary" onclick="agentTemplateSave()">Save</button>';
  html += '<button class="btn-cancel" onclick="agentTemplateDuplicate()">Duplicate</button>';
  if (!_agentTplNew) html += '<button class="btn-cancel btn-danger" onclick="agentTemplateDelete()">Delete</button>';
  html += '</div>';
  html += '</div>';

  el.innerHTML = html;
  _populateProviderSelect('agent-template-provider', d.provider || '', false);
  agentTemplateProviderChanged();
  el.querySelectorAll('textarea').forEach(_tplAutoResize);
}

function _agentTemplateTerminalRow(idx, term) {
  term = term || {};
  var html = '<div class="tpled-transition-entry" data-idx="' + idx + '">';
  html += '<button class="tpled-tr-remove" onclick="agentTemplateRemoveTerminal(' + idx + ')" title="Remove terminal">\u2715</button>';
  html += '<div class="tpled-transition-body">';
  html += '<label>Name</label><input class="agent-template-terminal-name" value="' + esc(term.name || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off">';
  html += '<label>Command</label><input class="agent-template-terminal-command" value="' + esc(term.command || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off">';
  html += '</div></div>';
  return html;
}

function agentTemplateAddTerminal() {
  var el = document.getElementById('agent-template-terminals');
  if (!el) return;
  var idx = el.querySelectorAll('.tpled-transition-entry').length;
  el.insertAdjacentHTML('beforeend', _agentTemplateTerminalRow(idx, {}));
  agentTemplateMarkDirty();
}

function agentTemplateRemoveTerminal(idx) {
  var el = document.getElementById('agent-template-terminals');
  if (!el) return;
  var rows = el.querySelectorAll('.tpled-transition-entry');
  if (rows[idx]) rows[idx].remove();
  agentTemplateMarkDirty();
}

function agentTemplateProviderChanged() {
  var sel = document.getElementById('agent-template-provider');
  var input = document.getElementById('agent-template-command');
  if (!sel || !input) return;
  var prov = sel.value;
  if (!prov) input.placeholder = 'Leave blank to use the default provider command';
  else input.placeholder = (_getProviderCommand('agent-template-provider') || prov) + ' (default)';
}

function agentTemplateMarkDirty() {
  _agentTplDirty = true;
}

function _agentTemplateReadForm() {
  var terminals = [];
  document.querySelectorAll('#agent-template-terminals .tpled-transition-entry').forEach(function(row) {
    var name = (row.querySelector('.agent-template-terminal-name').value || '').trim();
    var command = (row.querySelector('.agent-template-terminal-command').value || '').trim();
    if (name || command) terminals.push({ name: name, command: command });
  });
  return {
    name: (document.getElementById('agent-template-name').value || '').trim(),
    display_name: (document.getElementById('agent-template-display').value || '').trim(),
    description: (document.getElementById('agent-template-desc').value || '').trim(),
    provider: _getProviderValue('agent-template-provider'),
    command: (document.getElementById('agent-template-command').value || '').trim(),
    model: (document.getElementById('agent-template-model').value || '').trim(),
    permissions: (document.getElementById('agent-template-permissions').value || '').trim(),
    max_turns: parseInt(document.getElementById('agent-template-max-turns').value, 10) || 0,
    system_prompt: document.getElementById('agent-template-system-prompt').value || '',
    initial_prompt: document.getElementById('agent-template-initial-prompt').value || '',
    session_resume: document.getElementById('agent-template-session-resume').checked,
    idle_timeout: parseInt(document.getElementById('agent-template-idle-timeout').value, 10) || 0,
    tab_color: (document.getElementById('agent-template-color').value || '').trim(),
    icon: (document.getElementById('agent-template-icon').value || '').trim(),
    worktree: document.getElementById('agent-template-worktree').checked,
    worktree_base_branch: (document.getElementById('agent-template-worktree-base').value || '').trim(),
    worktree_auto_checkpoint: document.getElementById('agent-template-auto-checkpoint').checked,
    worktree_merge_squash: document.getElementById('agent-template-merge-squash').checked,
    env_vars: _textToEnv('agent-template-env'),
    terminals: terminals,
  };
}

function agentTemplateSave() {
  var data = _agentTemplateReadForm();
  var name = (data.name || '').replace(/[^a-zA-Z0-9_/.-]/g, '-').toLowerCase();
  if (!name) {
    document.getElementById('agent-template-name').focus();
    return;
  }
  data.name = name;
  var msg = {
    cmd: 'save_template',
    name: name,
    template: data,
    scope: document.getElementById('agent-template-scope').value || 'project',
    group: _currentGroup(),
  };
  var oldName = _agentTplSelectedName();
  var newScope = document.getElementById('agent-template-scope').value || 'project';
  if (!_agentTplNew && oldName && (oldName !== name || newScope !== _agentTplScope)) {
    msg.old_name = oldName;
  }
  _agentTplSelected = newScope + ':' + name;
  _agentTplScope = newScope;
  _agentTplDirty = false;
  _agentTplNew = false;
  send(msg);
}

function agentTemplateDelete() {
  var name = _agentTplSelectedName();
  showConfirm('Delete template "' + name + '"?').then(function(yes) {
    if (!yes) return;
    send({
      cmd: 'delete_template',
      name: name,
      scope: _agentTplScope,
      group: _currentGroup(),
    });
  });
}

function agentTemplateDuplicate() {
  _agentTplData = _agentTemplateReadForm();
  _agentTplData.name = (_agentTplData.name || 'template') + '-copy';
  _agentTplSelected = '';
  _agentTplNew = true;
  _agentTplDirty = true;
  renderAgentTemplatesPanel();
  var inp = document.getElementById('agent-template-name');
  if (inp) { inp.focus(); inp.select(); }
}


/* ------------------------------------------------------------------ */
/* Agent history view                                                  */
/* ------------------------------------------------------------------ */

var _agentHistoryRecords = [];
var _agentHistoryFilter = '';       // '', 'active', 'removed', 'merged'
var _agentHistorySearch = '';
var _agentHistoryExpanded = '';     // agent ID currently expanded
var _agentHistoryDetail = null;     // detail data for expanded agent

function _agentHistoryCurrentGroup() {
  if (typeof selectedAgentId === 'undefined' || !selectedAgentId) return '';
  if (!state || !state.agents || !state.agents[selectedAgentId]) return '';
  return state.agents[selectedAgentId].group || '';
}

function agentHistoryLoad() {
  send({
    cmd: 'get_agent_history',
    status: _agentHistoryFilter,
    limit: 100,
  });
}

function agentHistoryReceiveList(msg) {
  _agentHistoryRecords = msg.records || [];
  if (_agentsPanelView === 'history') renderAgentTemplatesPanel();
}

function agentHistoryReceiveDetail(msg) {
  _agentHistoryDetail = msg;
  renderAgentHistoryExpanded();
}

function _ahFmtTs(ts) {
  if (!ts) return '\u2014';
  var d = new Date(ts * 1000);
  var now = Date.now();
  var diff = now - d.getTime();
  if (diff < 60000) return 'just now';
  if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
  if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
  if (diff < 604800000) return Math.floor(diff / 86400000) + 'd ago';
  return d.toLocaleDateString();
}

function _ahFmtTokens(n) {
  if (!n) return '\u2014';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return '' + n;
}

function _ahStatusBadge(status) {
  var cls = 'ah-badge-removed';
  if (status === 'active') cls = 'ah-badge-active';
  else if (status === 'merged') cls = 'ah-badge-merged';
  return '<span class="ah-badge ' + cls + '">' + esc(status) + '</span>';
}

function _ahActionIcon(action) {
  var icons = {
    done: '\u2713', ready: '\u2713', blocked: '\u26A0',
    error: '\u2717', progress: '\u25B6', derive: '\u2192',
    ask: '\u2753', name: '\u270E'
  };
  return icons[action] || '\u2022';
}

function agentHistorySetFilter(f) {
  _agentHistoryFilter = f;
  agentHistoryLoad();
}

function agentHistoryOnSearch(val) {
  _agentHistorySearch = val.toLowerCase();
  renderAgentHistoryView();
}

function agentHistoryToggle(agentId) {
  if (_agentHistoryExpanded === agentId) {
    _agentHistoryExpanded = '';
    _agentHistoryDetail = null;
    renderAgentHistoryView();
  } else {
    _agentHistoryExpanded = agentId;
    _agentHistoryDetail = null;
    renderAgentHistoryView();
    send({ cmd: 'get_agent_history_detail', agent_id: agentId });
  }
}

function agentHistoryFocusAgent(agentId) {
  send({ cmd: 'focus_agent', id: agentId });
}

function renderAgentHistoryView() {
  var container = document.getElementById('agent-history-container');
  if (!container) return;

  var html = '';

  // Search + filter bar
  html += '<div class="ah-toolbar">';
  html += '<input class="ah-search" type="text" placeholder="Search agents\u2026" '
    + 'value="' + esc(_agentHistorySearch) + '" '
    + 'oninput="agentHistoryOnSearch(this.value)">';
  var filters = [
    ['', 'All'], ['active', 'Active'], ['removed', 'Removed'], ['merged', 'Merged']
  ];
  html += '<div class="ah-filters">';
  for (var i = 0; i < filters.length; i++) {
    var f = filters[i];
    html += '<button class="ah-filter-btn' + (_agentHistoryFilter === f[0] ? ' active' : '') + '" '
      + 'onclick="agentHistorySetFilter(\'' + f[0] + '\')">' + f[1] + '</button>';
  }
  html += '</div>';
  html += '</div>';

  // Filter records by search
  var records = _agentHistoryRecords;
  var group = _agentHistoryCurrentGroup();
  if (group) {
    records = records.filter(function(r) {
      return (r.group || '') === group;
    });
  }
  if (_agentHistorySearch) {
    records = records.filter(function(r) {
      return r.name.toLowerCase().indexOf(_agentHistorySearch) >= 0
        || (r.group || '').toLowerCase().indexOf(_agentHistorySearch) >= 0
        || (r.agent_type || '').toLowerCase().indexOf(_agentHistorySearch) >= 0;
    });
  }

  if (!records.length) {
    html += '<div class="ah-empty">No agent history records found.</div>';
    container.innerHTML = html;
    return;
  }

  // Agent list
  html += '<div class="ah-list">';
  for (var j = 0; j < records.length; j++) {
    var r = records[j];
    var expanded = _agentHistoryExpanded === r.id;
    var isActive = r.status === 'active';
    html += '<div class="ah-row' + (expanded ? ' expanded' : '') + '">';
    html += '<div class="ah-row-header" onclick="agentHistoryToggle(\'' + esc(r.id) + '\')">';
    html += '<span class="ah-expand-arrow">' + (expanded ? '\u25BC' : '\u25B6') + '</span>';
    html += '<span class="ah-name">' + esc(r.name) + '</span>';
    if (r.agent_type) {
      var typeLabel = (typeof AGENT_TYPE_LABELS !== 'undefined' && AGENT_TYPE_LABELS[r.agent_type]) || r.agent_type;
      html += '<span class="ah-type-badge">' + esc(typeLabel) + '</span>';
    }
    if (r.group) html += '<span class="ah-group">' + esc(r.group) + '</span>';
    html += _ahStatusBadge(r.status);
    html += '<span class="ah-meta">' + _ahFmtTs(r.created_at) + '</span>';
    html += '<span class="ah-meta" title="Tasks">' + (r.total_tasks || 0) + ' tasks</span>';
    var tokIn = r.total_tokens_in || 0;
    var tokOut = r.total_tokens_out || 0;
    if (tokIn || tokOut) {
      html += '<span class="ah-meta ah-tokens" title="Tokens in/out">'
        + _ahFmtTokens(tokIn) + '/' + _ahFmtTokens(tokOut) + '</span>';
    }
    if (isActive) {
      html += '<button class="ah-focus-btn" onclick="event.stopPropagation();agentHistoryFocusAgent(\'' + esc(r.id) + '\')" title="Focus agent">\u2192</button>';
    }
    html += '</div>';  // ah-row-header

    // Expanded detail area
    if (expanded) {
      html += '<div class="ah-detail" id="ah-detail-' + esc(r.id) + '">';
      if (!_agentHistoryDetail) {
        html += '<div class="ah-loading">Loading\u2026</div>';
      }
      html += '</div>';
    }

    html += '</div>';  // ah-row
  }
  html += '</div>';

  container.innerHTML = html;

  // If we have detail, render it
  if (_agentHistoryExpanded && _agentHistoryDetail) {
    renderAgentHistoryExpanded();
  }
}

function renderAgentHistoryExpanded() {
  var detail = _agentHistoryDetail;
  if (!detail || !detail.record) return;
  var el = document.getElementById('ah-detail-' + detail.record.id);
  if (!el) return;

  var r = detail.record;
  var html = '';

  // Info grid
  html += '<div class="ah-info">';
  html += '<div class="ah-info-row"><span class="ah-label">Template</span><span>' + esc(r.template || '\u2014') + '</span></div>';
  html += '<div class="ah-info-row"><span class="ah-label">Branch</span><span>' + esc(r.worktree_branch || '\u2014') + '</span></div>';
  html += '<div class="ah-info-row"><span class="ah-label">Created</span><span>' + _ahFmtTs(r.created_at) + '</span></div>';
  if (r.removed_at) {
    html += '<div class="ah-info-row"><span class="ah-label">Removed</span><span>' + _ahFmtTs(r.removed_at) + '</span></div>';
  }
  html += '<div class="ah-info-row"><span class="ah-label">Tokens</span><span>' + _ahFmtTokens(r.total_tokens_in) + ' in / ' + _ahFmtTokens(r.total_tokens_out) + ' out</span></div>';
  html += '</div>';

  // Tasks
  var tasks = detail.tasks || [];
  if (tasks.length) {
    html += '<div class="ah-section-title">Tasks (' + tasks.length + ')</div>';
    html += '<div class="ah-tasks">';
    for (var i = 0; i < tasks.length; i++) {
      var t = tasks[i];
      var outcome = t.outcome || 'in-progress';
      var outClass = 'ah-outcome-' + outcome.replace(/[^a-z]/g, '');
      html += '<div class="ah-task-row">';
      html += '<span class="ah-task-outcome ' + outClass + '">' + esc(outcome) + '</span>';
      html += '<span class="ah-task-title">' + esc(t.task_title) + '</span>';
      html += '<span class="ah-meta">' + _ahFmtTs(t.started_at) + '</span>';
      html += '</div>';
    }
    html += '</div>';
  }

  // Messages
  var messages = detail.messages || [];
  if (messages.length) {
    html += '<div class="ah-section-title">Messages (' + messages.length + ')</div>';
    html += '<div class="ah-messages">';
    for (var j = 0; j < messages.length; j++) {
      var m = messages[j];
      html += '<div class="ah-msg-row">';
      html += '<span class="ah-msg-icon">' + _ahActionIcon(m.action) + '</span>';
      html += '<span class="ah-msg-action">' + esc(m.action) + '</span>';
      html += '<span class="ah-msg-text">' + esc(m.message || '') + '</span>';
      html += '<span class="ah-meta">' + _ahFmtTs(m.timestamp) + '</span>';
      html += '</div>';
    }
    html += '</div>';
  }

  if (!tasks.length && !messages.length) {
    html += '<div class="ah-empty">No activity recorded.</div>';
  }

  el.innerHTML = html;
}

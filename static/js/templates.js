/* ------------------------------------------------------------------ */
/* Agent templates editor                                              */
/* ------------------------------------------------------------------ */

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

function renderAgentTemplatesPanel() {
  var panel = document.getElementById('panel-actions');
  if (!panel) return;

  var html = '';
  html += '<div class="panel-mode-tabs">';
  html += '<button class="panel-mode-tab" onclick="switchPanelEditorMode(\'actions\')">Actions</button>';
  html += '<button class="panel-mode-tab active" onclick="switchPanelEditorMode(\'templates\')">Agent Templates</button>';
  html += '</div>';

  html += '<div class="tpled-header">';
  html += '<span class="tpled-header-title">Agent Templates</span>';
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

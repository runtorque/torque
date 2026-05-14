/* ------------------------------------------------------------------ */
/* Role Library editor                                                */
/* ------------------------------------------------------------------ */

var _agentTplList = [];
var _agentTplSelected = '';
var _agentTplData = null;
var _agentTplDirty = false;
var _agentTplNew = false;
var _agentTplScope = 'project';
var _agentTplLoadedGroup = null;
var _agentTplLoadingGroup = null;
var _libraryActiveTab = 'roles';

var _specializationList = [];
var _specializationSelected = '';
var _specializationData = null;
var _specializationDirty = false;
var _specializationNew = false;
var _specializationScope = 'project';
var _specializationLoadedGroup = null;
var _specializationLoadingGroup = null;

function _libraryTabsHtml() {
  var rolesActive = _libraryActiveTab !== 'specializations';
  return '<div class="tpled-view-toggle library-tab-toggle">'
    + '<button class="tpled-view-btn' + (rolesActive ? ' active' : '') + '" onclick="librarySwitchTab(\'roles\')">Roles</button>'
    + '<button class="tpled-view-btn' + (!rolesActive ? ' active' : '') + '" onclick="librarySwitchTab(\'specializations\')">Specializations</button>'
    + '</div>';
}

function librarySwitchTab(tab) {
  _libraryActiveTab = tab === 'specializations' ? 'specializations' : 'roles';
  if (_libraryActiveTab === 'specializations') {
    specializationLibraryEnsureLoaded();
  } else {
    agentTemplateEnsureLoaded();
  }
  renderAgentTemplatesPanel();
}

function _agentTplKey(t) {
  return (t.global ? 'user:' : 'project:') + t.name;
}

function _agentTplSelectedName() {
  var idx = _agentTplSelected.indexOf(':');
  return idx >= 0 ? _agentTplSelected.slice(idx + 1) : _agentTplSelected;
}

function agentTemplateEditorLoad() {
  if (_libraryActiveTab === 'specializations') {
    specializationLibraryLoad();
    return;
  }
  var group = _currentGroup();
  _agentTplLoadingGroup = group || '';
  send({ cmd: 'get_config', group: group });
  send({ cmd: 'list_roles', group: group });
}

function agentTemplateEnsureLoaded() {
  if (_libraryActiveTab === 'specializations') {
    specializationLibraryEnsureLoaded();
    return;
  }
  var group = _currentGroup() || '';
  if (_agentTplLoadedGroup === group || _agentTplLoadingGroup === group) return;
  agentTemplateEditorLoad();
}

function agentTemplateBeginGroupSwitch() {
  var group = _currentGroup() || '';
  if (_agentTplLoadedGroup !== group) {
    _agentTplList = [];
    _agentTplSelected = '';
    _agentTplData = null;
    _agentTplDirty = false;
    _agentTplNew = false;
    _agentTplLoadedGroup = null;
  }
  if (_specializationLoadedGroup !== group) {
    _specializationList = [];
    _specializationSelected = '';
    _specializationData = null;
    _specializationDirty = false;
    _specializationNew = false;
    _specializationLoadedGroup = null;
  }
  agentTemplateEditorLoad();
  renderAgentTemplatesPanel();
}

function agentTemplateReceiveList(msg) {
  var msgGroup = (msg && msg.group != null) ? (msg.group || '') : (_currentGroup() || '');
  var currentGroup = _currentGroup() || '';
  if (msgGroup !== currentGroup) {
    if (_agentTplLoadingGroup === msgGroup) _agentTplLoadingGroup = null;
    return;
  }
  _agentTplLoadingGroup = null;
  _agentTplLoadedGroup = msgGroup;
  _agentTplList = msg.roles || msg.templates || [];
  if (msg.saved) {
    var match = _agentTplList.find(function(t) { return t.name === msg.saved; });
    if (match) _agentTplSelected = _agentTplKey(match);
  }
  if (msg.deleted && _agentTplSelectedName() === msg.deleted) {
    _agentTplSelected = '';
    _agentTplData = null;
  }
  if (_libraryActiveTab === 'specializations') return;
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
  if (_libraryActiveTab === 'specializations') return;
  if (msg.name !== _agentTplSelectedName()) return;
  _agentTplData = msg.template || {};
  _agentTplDirty = false;
  _agentTplNew = false;
  renderAgentTemplatesEditor();
}

function agentsPanelSwitchView(view) {
  // Backward-compatible shim for any stale Library-tab callers. History is
  // now a separate panel; the Library panel always shows roles.
  if (view === 'history') {
    if (typeof openHistoryPanel === 'function') openHistoryPanel();
    else if (typeof togglePanel === 'function') togglePanel('history');
    return;
  }
  if (view === 'specializations') {
    librarySwitchTab('specializations');
    return;
  }
  _libraryActiveTab = 'roles';
  agentTemplateEnsureLoaded();
  renderAgentTemplatesPanel();
}

function renderAgentTemplatesPanel() {
  var panel = document.getElementById('panel-templates');
  if (!panel) return;
  if (_libraryActiveTab === 'specializations') {
    renderSpecializationLibraryPanel();
    return;
  }
  var scopeGroup = (typeof _currentGroup === 'function' ? _currentGroup() : '') || '';
  var loadedForScope = _agentTplLoadedGroup === scopeGroup
    || (_agentTplLoadedGroup == null && _agentTplLoadingGroup !== scopeGroup);
  var listForScope = loadedForScope ? _agentTplList : [];
  var selectedForScope = loadedForScope ? _agentTplSelected : '';

  var html = '';
  html += '<div class="tpled-header">';
  html += '<div class="tpled-header-copy">';
  html += '<div class="tpled-header-title-row">';
  html += '<span class="tpled-header-title">Role Library</span>';
  html += _libraryTabsHtml();
  html += '</div>';
  html += '<div class="tpled-header-subtitle">Roles for launching agents. Live agents stay in the left column.</div>';
  html += '</div>';
  html += '<div class="tpled-header-controls">';

  html += '<select class="tpled-select" id="agent-tpl-select" onchange="agentTemplateSelect(this.value)">';
  html += '<option value="">Select\u2026</option>';
  var project = listForScope.filter(function(t) { return !t.global; });
  var user = listForScope.filter(function(t) { return t.global; });
  if (project.length) {
    html += '<optgroup label="Project">';
    for (var i = 0; i < project.length; i++) {
      var key = _agentTplKey(project[i]);
      html += '<option value="' + esc(key) + '"' + (key === selectedForScope ? ' selected' : '') + '>'
        + esc(project[i].display_name || project[i].name) + '</option>';
    }
    html += '</optgroup>';
  }
  if (user.length) {
    html += '<optgroup label="User">';
    for (var j = 0; j < user.length; j++) {
      var ukey = _agentTplKey(user[j]);
      var suffix = user[j].shadowed ? ' (overridden)' : '';
      html += '<option value="' + esc(ukey) + '"' + (ukey === selectedForScope ? ' selected' : '') + '>'
        + esc(user[j].display_name || user[j].name) + suffix + '</option>';
    }
    html += '</optgroup>';
  }
  html += '</select>';
  html += '<button class="tpled-new-btn" onclick="agentTemplateNew()" title="New role">+</button>';
  html += '<button class="tpled-new-btn" onclick="agentTemplateEditorLoad()" title="Refresh">&#x21BB;</button>';
  html += '</div>';
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

function _librarySelectedMeta(list, key, scope) {
  var idx = key.indexOf(':');
  var name = idx >= 0 ? key.slice(idx + 1) : key;
  var wantGlobal = scope === 'user';
  for (var i = 0; i < (list || []).length; i++) {
    var item = list[i] || {};
    if (item.name === name && !!item.global === wantGlobal) return item;
  }
  return null;
}

function _librarySourcePathFromMeta(meta, fallbackName) {
  meta = meta || {};
  if (meta.path) return meta.path;
  var dir = meta.dir || '';
  var name = meta.name || fallbackName || '';
  if (!dir || !name) return '';
  return String(dir).replace(/\/+$/, '') + '/' + name + '.yaml';
}

function _agentTemplateSourcePath() {
  var meta = _librarySelectedMeta(_agentTplList, _agentTplSelected, _agentTplScope);
  return _librarySourcePathFromMeta(meta, _agentTplSelectedName());
}

function renderAgentTemplatesEditor() {
  var el = document.getElementById('agent-tpl-editor');
  if (!el) return;
  var scopeGroup = (typeof _currentGroup === 'function' ? _currentGroup() : '') || '';
  var loadedForScope = _agentTplLoadedGroup === scopeGroup
    || (_agentTplLoadedGroup == null && _agentTplLoadingGroup !== scopeGroup);
  var listForScope = loadedForScope ? _agentTplList : [];
  var dataForScope = loadedForScope ? _agentTplData : null;
  var newForScope = loadedForScope ? _agentTplNew : false;
  if (!loadedForScope) {
    el.innerHTML = '<div class="tpled-empty">Loading roles\u2026</div>';
    return;
  }
  if (!dataForScope && !newForScope) {
    if (listForScope.length === 0) {
      el.innerHTML = '<div class="tpled-empty">No roles found.<br>Click <b>+</b> to save a launch preset,<br>or add <code>.yaml</code> files to <code>.torque/roles/</code>.</div>';
    } else {
      el.innerHTML = '<div class="tpled-empty">Pick a role from the library above.</div>';
    }
    return;
  }

  var d = dataForScope || {};
  var html = '<div class="tpled-form">';
  html += '<label>Name <span class="label-req">*</span></label>';
  html += '<input id="agent-template-name" value="' + esc(d.name || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off">';
  html += '<label>Scope</label>';
  html += '<select id="agent-template-scope" onchange="agentTemplateMarkDirty()">';
  html += '<option value="project"' + (_agentTplScope === 'project' ? ' selected' : '') + '>Project (.torque/roles/)</option>';
  html += '<option value="user"' + (_agentTplScope === 'user' ? ' selected' : '') + '>User (~/.torque/roles/)</option>';
  html += '</select>';
  var sourcePath = _agentTemplateSourcePath();
  if (sourcePath) {
    html += '<label>Source path</label>';
    html += '<input class="tpled-source-path" value="' + esc(sourcePath) + '" readonly>';
  }
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

  html += '<details class="tpled-section"' + (d.system_prompt || d.initial_prompt || d.preamble || (d.priorities || []).length ? ' open' : '') + '><summary>Behavior</summary>';
  html += '<label>Preamble (behavior) <span class="hint-btn" onclick="event.preventDefault();toggleHint(this)" data-hint="Injected at the top of dispatch prompts for workers with this role. Acts as a persistent persona layer, distinct from per-task actions.">?</span></label>';
  html += '<textarea id="agent-template-preamble" rows="4" oninput="_tplAutoResize(this)" onchange="agentTemplateMarkDirty()" placeholder="Behavior guidance for workers with this role. Rendered at the top of every dispatch. Supports Jinja.">' + esc(d.preamble || '') + '</textarea>';
  html += '<label>Priorities <span class="hint-btn" onclick="event.preventDefault();toggleHint(this)" data-hint="Ordered bullet list rendered inside the preamble. Short lines. Use for high-level reminders.">?</span></label>';
  html += '<div id="agent-template-priorities">';
  for (var pi = 0; pi < (d.priorities || []).length; pi++) {
    html += _agentTemplatePriorityRow(pi, d.priorities[pi]);
  }
  html += '</div>';
  html += '<button class="tpled-transition-add" onclick="agentTemplateAddPriority()">+ Add priority</button>';
  html += '<label>System prompt</label><textarea id="agent-template-system-prompt" rows="4" oninput="_tplAutoResize(this)" onchange="agentTemplateMarkDirty()">' + esc(d.system_prompt || '') + '</textarea>';
  html += '<label>Initial prompt</label><textarea id="agent-template-initial-prompt" rows="3" oninput="_tplAutoResize(this)" onchange="agentTemplateMarkDirty()">' + esc(d.initial_prompt || '') + '</textarea>';
  html += '<label class="gs-checkbox"><input id="agent-template-session-resume" type="checkbox"' + (d.session_resume !== false ? ' checked' : '') + ' onchange="agentTemplateMarkDirty()"> Resume session on relaunch</label>';
  html += '<label>Idle timeout <span class="label-hint">minutes</span></label><input id="agent-template-idle-timeout" type="number" min="0" value="' + esc(d.idle_timeout != null ? d.idle_timeout : 0) + '" onchange="agentTemplateMarkDirty()">';
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
  html += '<button class="tpled-tr-remove" onclick="agentTemplateRemoveTerminal(' + idx + ')" title="Delete terminal">\u2715</button>';
  html += '<div class="tpled-transition-body">';
  html += '<label>Name</label><input class="agent-template-terminal-name" value="' + esc(term.name || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off">';
  html += '<label>Command</label><input class="agent-template-terminal-command" value="' + esc(term.command || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off">';
  html += '</div></div>';
  return html;
}

function _agentTemplatePriorityRow(idx, value) {
  var html = '<div class="tpled-priority-row" data-idx="' + idx + '">';
  html += '<span class="tpled-priority-grip" aria-hidden="true">\u22EE\u22EE</span>';
  html += '<input type="text" class="agent-template-priority-input" value="' + esc(value || '') + '" onchange="agentTemplateMarkDirty()" autocomplete="off" placeholder="e.g. ship small">';
  html += '<button class="tpled-priority-remove" onclick="agentTemplateRemovePriority(' + idx + ')" title="Delete priority">\u2715</button>';
  html += '</div>';
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

function agentTemplateAddPriority() {
  var el = document.getElementById('agent-template-priorities');
  if (!el) return;
  var idx = el.querySelectorAll('.tpled-priority-row').length;
  el.insertAdjacentHTML('beforeend', _agentTemplatePriorityRow(idx, ''));
  agentTemplateMarkDirty();
}

function agentTemplateRemovePriority(idx) {
  var el = document.getElementById('agent-template-priorities');
  if (!el) return;
  var rows = el.querySelectorAll('.tpled-priority-row');
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
  var priorities = [];
  document.querySelectorAll('#agent-template-terminals .tpled-transition-entry').forEach(function(row) {
    var name = (row.querySelector('.agent-template-terminal-name').value || '').trim();
    var command = (row.querySelector('.agent-template-terminal-command').value || '').trim();
    if (name || command) terminals.push({ name: name, command: command });
  });
  document.querySelectorAll('#agent-template-priorities .tpled-priority-row').forEach(function(row) {
    var input = row.querySelector('.agent-template-priority-input');
    var value = input ? (input.value || '').trim() : '';
    if (value) priorities.push(value);
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
    preamble: document.getElementById('agent-template-preamble').value || '',
    priorities: priorities,
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
    cmd: 'save_role',
    name: name,
    role: data,
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
  showConfirm('Delete role "' + name + '"?').then(function(yes) {
    if (!yes) return;
    send({
      cmd: 'delete_role',
      name: name,
      scope: _agentTplScope,
      group: _currentGroup(),
    });
  });
}

function agentTemplateDuplicate() {
  _agentTplData = _agentTemplateReadForm();
  _agentTplData.name = (_agentTplData.name || 'role') + '-copy';
  _agentTplSelected = '';
  _agentTplNew = true;
  _agentTplDirty = true;
  renderAgentTemplatesPanel();
  var inp = document.getElementById('agent-template-name');
  if (inp) { inp.focus(); inp.select(); }
}

function _specializationKey(s) {
  return (s.global ? 'user:' : 'project:') + s.name;
}

function _specializationSelectedName() {
  var idx = _specializationSelected.indexOf(':');
  return idx >= 0 ? _specializationSelected.slice(idx + 1) : _specializationSelected;
}

function _specializationSourcePath() {
  var meta = _librarySelectedMeta(
    _specializationList,
    _specializationSelected,
    _specializationScope
  );
  return _librarySourcePathFromMeta(meta, _specializationSelectedName());
}

function specializationLibraryLoad() {
  var group = _currentGroup();
  _specializationLoadingGroup = group || '';
  send({ cmd: 'list_specializations', group: group });
}

function specializationLibraryEnsureLoaded() {
  var group = _currentGroup() || '';
  if (_specializationLoadedGroup === group || _specializationLoadingGroup === group) return;
  specializationLibraryLoad();
}

function specializationLibraryReceiveList(msg) {
  var msgGroup = (msg && msg.group != null) ? (msg.group || '') : (_currentGroup() || '');
  var currentGroup = _currentGroup() || '';
  if (msgGroup !== currentGroup) {
    if (_specializationLoadingGroup === msgGroup) _specializationLoadingGroup = null;
    return;
  }
  _specializationLoadingGroup = null;
  _specializationLoadedGroup = msgGroup;
  _specializationList = msg.specializations || [];
  if (msg.saved) {
    var wantGlobal = _specializationScope === 'user';
    var match = _specializationList.find(function(s) {
      return s.name === msg.saved && !!s.global === wantGlobal;
    }) || _specializationList.find(function(s) { return s.name === msg.saved; });
    if (match) {
      _specializationSelected = _specializationKey(match);
      _specializationScope = match.global ? 'user' : 'project';
    }
  }
  if (msg.deleted && _specializationSelectedName() === msg.deleted) {
    _specializationSelected = '';
    _specializationData = null;
  }
  if (_libraryActiveTab !== 'specializations') return;
  renderAgentTemplatesPanel();
  if (_specializationSelected && !_specializationNew) {
    send({
      cmd: 'get_specialization',
      name: _specializationSelectedName(),
      group: _currentGroup(),
      scope: _specializationScope,
    });
  }
}

function specializationLibraryReceiveDetail(msg) {
  if (_libraryActiveTab !== 'specializations') return;
  if (msg.name !== _specializationSelectedName()) return;
  _specializationData = msg.specialization || {};
  _specializationDirty = false;
  _specializationNew = false;
  renderSpecializationLibraryEditor();
}

function renderSpecializationLibraryPanel() {
  var panel = document.getElementById('panel-templates');
  if (!panel) return;
  var scopeGroup = (typeof _currentGroup === 'function' ? _currentGroup() : '') || '';
  var loadedForScope = _specializationLoadedGroup === scopeGroup
    || (_specializationLoadedGroup == null && _specializationLoadingGroup !== scopeGroup);
  var listForScope = loadedForScope ? _specializationList : [];
  var selectedForScope = loadedForScope ? _specializationSelected : '';

  var html = '';
  html += '<div class="tpled-header">';
  html += '<div class="tpled-header-copy">';
  html += '<div class="tpled-header-title-row">';
  html += '<span class="tpled-header-title">Specializations</span>';
  html += _libraryTabsHtml();
  html += '</div>';
  html += '<div class="tpled-header-subtitle">Engineer routing hints and preambles for architect-created tasks.</div>';
  html += '</div>';
  html += '<div class="tpled-header-controls">';
  html += '<select class="tpled-select" id="specialization-select" onchange="specializationLibrarySelect(this.value)">';
  html += '<option value="">Select\u2026</option>';
  var project = listForScope.filter(function(s) { return !s.global; });
  var user = listForScope.filter(function(s) { return s.global; });
  if (project.length) {
    html += '<optgroup label="Project">';
    for (var i = 0; i < project.length; i++) {
      var key = _specializationKey(project[i]);
      html += '<option value="' + esc(key) + '"' + (key === selectedForScope ? ' selected' : '') + '>'
        + esc(project[i].name) + '</option>';
    }
    html += '</optgroup>';
  }
  if (user.length) {
    html += '<optgroup label="User">';
    for (var j = 0; j < user.length; j++) {
      var ukey = _specializationKey(user[j]);
      var suffix = user[j].shadowed ? ' (overridden)' : '';
      html += '<option value="' + esc(ukey) + '"' + (ukey === selectedForScope ? ' selected' : '') + '>'
        + esc(user[j].name) + suffix + '</option>';
    }
    html += '</optgroup>';
  }
  html += '</select>';
  html += '<button class="tpled-new-btn" onclick="specializationLibraryNew()" title="New specialization">+</button>';
  html += '<button class="tpled-new-btn" onclick="specializationLibraryLoad()" title="Refresh">&#x21BB;</button>';
  html += '</div>';
  html += '</div>';
  html += '<div class="tpled-editor" id="specialization-editor"></div>';

  panel.innerHTML = html;
  renderSpecializationLibraryEditor();
}

function specializationLibrarySelect(key) {
  if (!key) {
    _specializationSelected = '';
    _specializationData = null;
    _specializationNew = false;
    _specializationDirty = false;
    renderSpecializationLibraryEditor();
    return;
  }
  _specializationSelected = key;
  var parts = key.split(':');
  _specializationScope = parts[0] === 'user' ? 'user' : 'project';
  _specializationNew = false;
  _specializationData = null;
  _specializationDirty = false;
  renderAgentTemplatesPanel();
  send({
    cmd: 'get_specialization',
    name: parts.slice(1).join(':'),
    group: _currentGroup(),
    scope: _specializationScope,
  });
}

function specializationLibraryNew() {
  _specializationSelected = '';
  _specializationNew = true;
  _specializationDirty = true;
  _specializationScope = 'project';
  _specializationData = { name: '', description: '', preamble: '', priorities: [] };
  renderAgentTemplatesPanel();
  var inp = document.getElementById('specialization-name');
  if (inp) inp.focus();
}

function renderSpecializationLibraryEditor() {
  var el = document.getElementById('specialization-editor');
  if (!el) return;
  var scopeGroup = (typeof _currentGroup === 'function' ? _currentGroup() : '') || '';
  var loadedForScope = _specializationLoadedGroup === scopeGroup
    || (_specializationLoadedGroup == null && _specializationLoadingGroup !== scopeGroup);
  var listForScope = loadedForScope ? _specializationList : [];
  var dataForScope = loadedForScope ? _specializationData : null;
  var newForScope = loadedForScope ? _specializationNew : false;
  if (!loadedForScope) {
    el.innerHTML = '<div class="tpled-empty">Loading specializations\u2026</div>';
    return;
  }
  if (!dataForScope && !newForScope) {
    if (listForScope.length === 0) {
      el.innerHTML = '<div class="tpled-empty">No specializations found.<br>Click <b>+</b> to create one,<br>or add <code>.yaml</code> files to <code>.torque/specializations/</code>.</div>';
    } else {
      el.innerHTML = '<div class="tpled-empty">Pick a specialization from the library above.</div>';
    }
    return;
  }

  var d = dataForScope || {};
  var sourcePath = _specializationSourcePath();
  var priorities = (d.priorities || []).join('\n');
  var html = '<div class="tpled-form">';
  html += '<label>Name <span class="label-req">*</span></label>';
  html += '<input id="specialization-name" value="' + esc(d.name || '') + '" onchange="specializationLibraryMarkDirty()" autocomplete="off">';
  html += '<label>Scope</label>';
  html += '<select id="specialization-scope" onchange="specializationLibraryMarkDirty()">';
  html += '<option value="project"' + (_specializationScope === 'project' ? ' selected' : '') + '>Project (.torque/specializations/)</option>';
  html += '<option value="user"' + (_specializationScope === 'user' ? ' selected' : '') + '>User (~/.torque/specializations/)</option>';
  html += '</select>';
  if (sourcePath) {
    html += '<label>Source path</label>';
    html += '<input class="tpled-source-path" value="' + esc(sourcePath) + '" readonly>';
  }
  html += '<label>Description</label>';
  html += '<input id="specialization-description" value="' + esc(d.description || '') + '" onchange="specializationLibraryMarkDirty()" autocomplete="off">';
  html += '<label>Preamble</label>';
  html += '<textarea id="specialization-preamble" rows="5" oninput="_tplAutoResize(this)" onchange="specializationLibraryMarkDirty()" placeholder="Behavior guidance injected into engineer prompts that carry this specialization.">' + esc(d.preamble || '') + '</textarea>';
  html += '<label>Priorities <span class="label-hint">one per line</span></label>';
  html += '<textarea id="specialization-priorities" rows="4" oninput="_tplAutoResize(this)" onchange="specializationLibraryMarkDirty()" placeholder="rerender hygiene&#10;test first">' + esc(priorities) + '</textarea>';
  html += '<div class="tpled-actions">';
  html += '<button class="btn-primary" onclick="specializationLibrarySave()">Save</button>';
  html += '<button class="btn-cancel" onclick="specializationLibraryDuplicate()">Duplicate</button>';
  if (!_specializationNew) html += '<button class="btn-cancel btn-danger" onclick="specializationLibraryDelete()">Delete</button>';
  html += '</div>';
  html += '</div>';
  el.innerHTML = html;
  el.querySelectorAll('textarea').forEach(_tplAutoResize);
}

function specializationLibraryMarkDirty() {
  _specializationDirty = true;
}

function _specializationLibraryReadForm() {
  var prioritiesRaw = document.getElementById('specialization-priorities').value || '';
  var priorities = prioritiesRaw.split(/\n+/).map(function(line) {
    return line.trim();
  }).filter(Boolean);
  return {
    name: (document.getElementById('specialization-name').value || '').trim(),
    description: (document.getElementById('specialization-description').value || '').trim(),
    preamble: document.getElementById('specialization-preamble').value || '',
    priorities: priorities,
  };
}

function specializationLibrarySave() {
  var data = _specializationLibraryReadForm();
  var name = (data.name || '').replace(/[^a-zA-Z0-9_/.-]/g, '-').toLowerCase();
  if (!name) {
    document.getElementById('specialization-name').focus();
    return;
  }
  data.name = name;
  var oldName = _specializationSelectedName();
  var newScope = document.getElementById('specialization-scope').value || 'project';
  var msg = {
    cmd: 'save_specialization',
    name: name,
    data: data,
    scope: newScope,
    group: _currentGroup(),
  };
  if (!_specializationNew && oldName && (oldName !== name || newScope !== _specializationScope)) {
    msg.old_name = oldName;
    msg.old_scope = _specializationScope;
  }
  _specializationSelected = newScope + ':' + name;
  _specializationScope = newScope;
  _specializationDirty = false;
  _specializationNew = false;
  send(msg);
}

function specializationLibraryDelete() {
  var name = _specializationSelectedName();
  showConfirm('Delete specialization "' + name + '"?').then(function(yes) {
    if (!yes) return;
    send({
      cmd: 'delete_specialization',
      name: name,
      scope: _specializationScope,
      group: _currentGroup(),
    });
  });
}

function specializationLibraryDuplicate() {
  _specializationData = _specializationLibraryReadForm();
  _specializationData.name = (_specializationData.name || 'specialization') + '-copy';
  _specializationSelected = '';
  _specializationNew = true;
  _specializationDirty = true;
  renderAgentTemplatesPanel();
  var inp = document.getElementById('specialization-name');
  if (inp) { inp.focus(); inp.select(); }
}

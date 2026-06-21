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

var _agentClassList = [];
var _agentClassIssues = [];
var _agentClassSelected = '';
var _agentClassPreview = null;
var _agentClassEditorNew = false;
var _agentClassEditorDirty = false;
var _agentClassEditorMessage = '';
var _agentClassEditorError = '';
var _agentClassValidation = null;
var _agentClassValidationSignature = '';
var _agentClassValidationInFlight = false;
var _agentClassValidationRequestId = '';
var _agentClassLoadedBaseDir = null;
var _agentClassLoadingBaseDir = null;
var _agentClassProfileList = [];
var _agentClassProfileIssues = [];
var _agentClassProfileLoadedBaseDir = null;
var _agentClassProfileLoadingBaseDir = null;
var _agentClassSkipNextDraftCapture = false;
var _agentClassLaunchDrafts = {};
var _agentClassLaunchResult = null;
var _agentClassPickerSelections = {};
var _agentClassPickerContexts = {};
var _agentClassPickerLoading = false;
var _agentClassPickerRequestedBaseDir = '';
var _agentClassLastMutationRequestId = '';


var _specializationList = [];
var _specializationSelected = '';
var _specializationData = null;
var _specializationDirty = false;
var _specializationNew = false;
var _specializationScope = 'project';
var _specializationLoadedGroup = null;
var _specializationLoadingGroup = null;
var _specializationSkipNextDraftCapture = false;

function _libraryTabsHtml() {
  var rolesActive = _libraryActiveTab === 'roles';
  var specsActive = _libraryActiveTab === 'specializations';
  var classesActive = _libraryActiveTab === 'agent_classes';
  return '<div class="tpled-view-toggle library-tab-toggle">'
    + '<button class="tpled-view-btn' + (rolesActive ? ' active' : '') + '" onclick="librarySwitchTab(\'roles\')">Roles</button>'
    + '<button class="tpled-view-btn' + (specsActive ? ' active' : '') + '" onclick="librarySwitchTab(\'specializations\')">Specializations</button>'
    + '<button class="tpled-view-btn' + (classesActive ? ' active' : '') + '" onclick="librarySwitchTab(\'agent_classes\')">Agent Classes</button>'
    + '</div>';
}

function librarySwitchTab(tab) {
  if (tab === 'specializations') _libraryActiveTab = 'specializations';
  else if (tab === 'agent_classes') _libraryActiveTab = 'agent_classes';
  else _libraryActiveTab = 'roles';
  if (_libraryActiveTab === 'specializations') {
    specializationLibraryEnsureLoaded();
  } else if (_libraryActiveTab === 'agent_classes') {
    agentClassManagerEnsureLoaded();
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
  if (_libraryActiveTab === 'agent_classes') {
    agentClassManagerLoad(true);
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
  if (_libraryActiveTab === 'agent_classes') {
    agentClassManagerEnsureLoaded();
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
    _specializationSkipNextDraftCapture = true;
  }
  _agentClassLoadedBaseDir = null;
  _agentClassLoadingBaseDir = null;
  _agentClassList = [];
  _agentClassIssues = [];
  _agentClassSelected = '';
  _agentClassPreview = null;
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
  if (_libraryActiveTab !== 'roles') return;
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
  if (_libraryActiveTab !== 'roles') return;
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
  if (view === 'agent_classes' || view === 'classes') {
    librarySwitchTab('agent_classes');
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
  if (_libraryActiveTab === 'agent_classes') {
    renderAgentClassesPanel();
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
    _specializationSkipNextDraftCapture = true;
    _specializationSelected = '';
    _specializationData = null;
    _specializationDirty = false;
  }
  if (_libraryActiveTab !== 'specializations') return;
  renderAgentTemplatesPanel();
  if (_specializationSelected && !_specializationNew && !_specializationDirty) {
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
  if (_specializationDirty && document.getElementById('specialization-name')) return;
  _specializationData = msg.specialization || {};
  _specializationDirty = false;
  _specializationNew = false;
  renderSpecializationLibraryEditor();
}

function renderSpecializationLibraryPanel() {
  var panel = document.getElementById('panel-templates');
  if (!panel) return;
  var restoreState = _specializationSkipNextDraftCapture
    ? null
    : _specializationCaptureEditorUiState();
  _specializationSkipNextDraftCapture = false;
  if (restoreState && restoreState.form) {
    _specializationApplyEditorDraft(restoreState.form);
  }
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
  renderSpecializationLibraryEditor(restoreState);
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
  _specializationSkipNextDraftCapture = true;
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
  _specializationSkipNextDraftCapture = true;
  renderAgentTemplatesPanel();
  var inp = document.getElementById('specialization-name');
  if (inp) inp.focus();
}

function renderSpecializationLibraryEditor(restoreState) {
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
  html += '<input id="specialization-name" value="' + esc(d.name || '') + '" oninput="specializationLibraryMarkDirty()" onchange="specializationLibraryMarkDirty()" autocomplete="off">';
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
  html += '<input id="specialization-description" value="' + esc(d.description || '') + '" oninput="specializationLibraryMarkDirty()" onchange="specializationLibraryMarkDirty()" autocomplete="off">';
  html += '<label>Preamble</label>';
  html += '<textarea id="specialization-preamble" rows="5" oninput="_tplAutoResize(this);specializationLibraryMarkDirty()" onchange="specializationLibraryMarkDirty()" placeholder="Behavior guidance injected into engineer prompts that carry this specialization.">' + esc(d.preamble || '') + '</textarea>';
  html += '<label>Priorities <span class="label-hint">one per line</span></label>';
  html += '<textarea id="specialization-priorities" rows="4" oninput="_tplAutoResize(this);specializationLibraryMarkDirty()" onchange="specializationLibraryMarkDirty()" placeholder="rerender hygiene&#10;test first">' + esc(priorities) + '</textarea>';
  html += '<div class="tpled-actions">';
  html += '<button class="btn-primary" onclick="specializationLibrarySave()">Save</button>';
  html += '<button class="btn-cancel" onclick="specializationLibraryDuplicate()">Duplicate</button>';
  if (!_specializationNew) html += '<button class="btn-cancel btn-danger" onclick="specializationLibraryDelete()">Delete</button>';
  html += '</div>';
  html += '</div>';
  el.innerHTML = html;
  _specializationRestoreEditorUiState(el, restoreState);
  el.querySelectorAll('textarea').forEach(_tplAutoResize);
}

function specializationLibraryMarkDirty() {
  _specializationDirty = true;
  _specializationSkipNextDraftCapture = false;
}

function _specializationEditorField(id) {
  return document.getElementById ? document.getElementById(id) : null;
}

function _specializationPrioritiesFromText(value) {
  return String(value || '').split(/\n+/).map(function(line) {
    return line.trim();
  }).filter(Boolean);
}

function _specializationCaptureEditorUiState() {
  var snapshot = { form: null, focus: null };
  var nameEl = _specializationEditorField('specialization-name');
  var scopeEl = _specializationEditorField('specialization-scope');
  var descriptionEl = _specializationEditorField('specialization-description');
  var preambleEl = _specializationEditorField('specialization-preamble');
  var prioritiesEl = _specializationEditorField('specialization-priorities');
  if (nameEl && scopeEl && descriptionEl && preambleEl && prioritiesEl) {
    snapshot.form = {
      name: nameEl.value || '',
      scope: scopeEl.value || _specializationScope || 'project',
      description: descriptionEl.value || '',
      preamble: preambleEl.value || '',
      prioritiesText: prioritiesEl.value || '',
    };
  }
  var root = document.getElementById('specialization-editor')
    || document.getElementById('panel-templates');
  var active = document.activeElement;
  if (active && root && typeof root.contains === 'function' && root.contains(active)) {
    var focusKey = active.id || (active.dataset ? active.dataset.focusKey : '');
    if (focusKey) {
      snapshot.focus = {
        key: focusKey,
        byId: !!active.id,
        value: ('value' in active) ? active.value : null,
        checked: ('checked' in active) ? !!active.checked : null,
        selectionStart: typeof active.selectionStart === 'number' ? active.selectionStart : null,
        selectionEnd: typeof active.selectionEnd === 'number' ? active.selectionEnd : null,
        selectionDirection: active.selectionDirection || 'none',
      };
    }
  }
  return snapshot;
}

function _specializationApplyEditorDraft(form) {
  if (!form) return;
  var next = {
    name: String(form.name || ''),
    description: String(form.description || ''),
    preamble: String(form.preamble || ''),
    priorities: _specializationPrioritiesFromText(form.prioritiesText),
  };
  var nextScope = form.scope === 'user' ? 'user' : 'project';
  var current = _specializationData || {};
  var currentPriorities = Array.isArray(current.priorities)
    ? current.priorities
    : [];
  var prioritiesChanged = currentPriorities.length !== next.priorities.length
    || currentPriorities.some(function(item, idx) {
      return item !== next.priorities[idx];
    });
  if (
    String(current.name || '') !== next.name
    || String(current.description || '') !== next.description
    || String(current.preamble || '') !== next.preamble
    || prioritiesChanged
    || _specializationScope !== nextScope
  ) {
    _specializationDirty = true;
  }
  _specializationData = next;
  _specializationScope = nextScope;
}

function _specializationRestoreEditorUiState(root, snapshot) {
  if (!root || !snapshot) return;
  var form = snapshot.form || null;
  if (form) {
    var fields = {
      'specialization-name': form.name || '',
      'specialization-scope': form.scope || _specializationScope || 'project',
      'specialization-description': form.description || '',
      'specialization-preamble': form.preamble || '',
      'specialization-priorities': form.prioritiesText || '',
    };
    Object.keys(fields).forEach(function(id) {
      var el = _specializationEditorField(id);
      if (el && 'value' in el) el.value = fields[id];
    });
  }
  if (!snapshot.focus) return;
  var focus = snapshot.focus;
  var focusEl = null;
  if (focus.byId && document.getElementById) {
    focusEl = document.getElementById(focus.key);
  }
  if (!focusEl && root.querySelector) {
    focusEl = root.querySelector('[data-focus-key="' + focus.key + '"]');
  }
  if (!focusEl) return;
  if (focus.value != null && 'value' in focusEl) focusEl.value = focus.value;
  if (focus.checked != null && 'checked' in focusEl) focusEl.checked = focus.checked;
  if (typeof focusEl.focus === 'function') {
    try { focusEl.focus({ preventScroll: true }); }
    catch (_e) { focusEl.focus(); }
  }
  if (typeof focus.selectionStart === 'number' && 'selectionStart' in focusEl) {
    focusEl.selectionStart = focus.selectionStart;
  }
  if (typeof focus.selectionEnd === 'number' && 'selectionEnd' in focusEl) {
    focusEl.selectionEnd = focus.selectionEnd;
  }
  if (focus.selectionDirection && 'selectionDirection' in focusEl) {
    focusEl.selectionDirection = focus.selectionDirection;
  }
}

function _specializationLibraryReadForm() {
  var prioritiesRaw = document.getElementById('specialization-priorities').value || '';
  var priorities = _specializationPrioritiesFromText(prioritiesRaw);
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
  _specializationData = data;
  _specializationDirty = false;
  _specializationNew = false;
  _specializationSkipNextDraftCapture = true;
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

/* ------------------------------------------------------------------ */
/* Agent Class Library / trusted authoring UI                         */
/* ------------------------------------------------------------------ */

function agentClassBaseDirForGroup(group, config) {
  config = config || {};
  var direct = String(
    config.base_dir
    || config.project_base_dir
    || config.current_path
    || ''
  ).trim();
  if (direct) return direct;
  group = String(group || (typeof _currentGroup === 'function' ? _currentGroup() : '') || '').trim();
  var settings = (state && state.group_settings && group) ? (state.group_settings[group] || {}) : {};
  var candidates = [
    settings.project_base_dir,
    settings.default_directory,
    settings.agent_directory,
    settings.engineer_directory,
    settings.architect_directory,
    settings.worker_directory,
  ];
  if (state && state.agents) {
    Object.keys(state.agents).some(function(id) {
      var cell = state.agents[id] || {};
      if (group && String(cell.group || '') !== group) return false;
      var cellDir = String(cell.worktree_repo_root || cell.directory || cell.current_path || '').trim();
      if (cellDir) {
        candidates.push(cellDir);
        return true;
      }
      return false;
    });
  }
  for (var i = 0; i < candidates.length; i++) {
    var value = String(candidates[i] || '').trim();
    if (value) return value;
  }
  return '';
}

function _agentClassCurrentBaseDir() {
  return agentClassBaseDirForGroup(typeof _currentGroup === 'function' ? _currentGroup() : '');
}

function _agentClassSendWithBaseDir(payload, baseDir) {
  payload = payload || {};
  baseDir = String(baseDir || _agentClassCurrentBaseDir() || '').trim();
  if (baseDir) payload.base_dir = baseDir;
  if (typeof send === 'function') send(payload);
}

function agentClassManagerLoad(force) {
  var baseDir = _agentClassCurrentBaseDir();
  if (!force && (_agentClassLoadingBaseDir === baseDir || _agentClassLoadedBaseDir === baseDir)) return;
  _agentClassLoadingBaseDir = baseDir;
  _agentClassEditorError = '';
  _agentClassSendWithBaseDir({ cmd: 'agent_class_list' }, baseDir);
  agentClassManagerRequestProfiles(baseDir, force);
}

function agentClassManagerEnsureLoaded() {
  agentClassManagerLoad(false);
}

function agentClassManagerRequestProfiles(baseDir, force) {
  baseDir = String(baseDir || _agentClassCurrentBaseDir() || '').trim();
  if (!force && (_agentClassProfileLoadingBaseDir === baseDir || _agentClassProfileLoadedBaseDir === baseDir)) return;
  _agentClassProfileLoadingBaseDir = baseDir;
  _agentClassSendWithBaseDir({ cmd: 'agent_profile_list' }, baseDir);
}

function agentClassManagerReceiveProfiles(msg) {
  msg = msg || {};
  if (_agentClassProfileLoadingBaseDir == null) return;
  _agentClassProfileList = Array.isArray(msg.profiles) ? msg.profiles.slice() : [];
  _agentClassProfileIssues = Array.isArray(msg.issues) ? msg.issues.slice() : [];
  _agentClassProfileLoadedBaseDir = _agentClassProfileLoadingBaseDir || _agentClassCurrentBaseDir();
  _agentClassProfileLoadingBaseDir = null;
  agentClassRenderOpenPickers();
  if (_libraryActiveTab === 'agent_classes') renderAgentClassesPanel();
}

function _agentClassSort(list) {
  return (list || []).slice().sort(function(a, b) {
    var sa = a && a.builtin ? 0 : 1;
    var sb = b && b.builtin ? 0 : 1;
    if (sa !== sb) return sa - sb;
    var ak = String((a && a.base_kind) || '');
    var bk = String((b && b.base_kind) || '');
    if (ak !== bk) return ak.localeCompare(bk);
    return String((a && (a.display_name || a.id)) || '')
      .localeCompare(String((b && (b.display_name || b.id)) || ''));
  });
}

function _agentClassById(classId) {
  classId = String(classId || '').trim();
  for (var i = 0; i < _agentClassList.length; i++) {
    var item = _agentClassList[i] || {};
    if (String(item.id || '') === classId) return item;
  }
  return null;
}

function _agentClassDisplayName(item, fallback) {
  item = item || {};
  return String(
    item.primary_identity_label
    || item.primary_display_name
    || item.display_name
    || item.title
    || item.name
    || item.id
    || fallback
    || ''
  ).trim();
}

function _agentClassStatus(item) {
  item = item || {};
  if (item.archived || item.disabled) return 'archived';
  return String(item.status || item.lifecycle || '').trim() || 'full';
}

function _agentClassIsArchived(item) {
  item = item || {};
  var metadata = item.metadata && typeof item.metadata === 'object' ? item.metadata : {};
  return !!(item.archived || item.disabled || metadata.archived || metadata.disabled || metadata.archived_at);
}

function _agentClassLaunchDisabledReason(item, expectedKind) {
  item = item || {};
  expectedKind = String(expectedKind || '').trim();
  if (!item.id) return 'Select an Agent Class first.';
  if (expectedKind && String(item.base_kind || '') !== expectedKind) return 'Agent Class base kind does not match this launch flow.';
  if (_agentClassIsArchived(item)) return 'Archived/disabled Agent Classes cannot launch.';
  if (item.launchable === false) return 'Backend reports this Agent Class is not launchable.';
  if (_agentClassStatus(item) === 'invalid') return 'Invalid Agent Classes cannot launch.';
  return '';
}

function _agentClassDefaultProfileId(kind) {
  kind = String(kind || '').trim();
  return (kind === 'architect' || kind === 'engineer' || kind === 'worker') ? ('full-' + kind) : '';
}

function _agentClassCompatibleProfiles(kind) {
  kind = String(kind || '').trim();
  var out = [];
  for (var i = 0; i < _agentClassProfileList.length; i++) {
    var profile = _agentClassProfileList[i] || {};
    if (String(profile.base_kind || '') === kind) out.push(profile);
  }
  out.sort(function(a, b) {
    return String(a.display_name || a.id || '').localeCompare(String(b.display_name || b.id || ''));
  });
  return out;
}

function _agentClassProfileById(profileId) {
  profileId = String(profileId || '').trim();
  for (var i = 0; i < _agentClassProfileList.length; i++) {
    var profile = _agentClassProfileList[i] || {};
    if (String(profile.id || '') === profileId) return profile;
  }
  return null;
}

function _agentClassProfileVersion(profileId, fallback) {
  var profile = _agentClassProfileById(profileId);
  return String((profile && profile.version) || fallback || '').trim();
}

function _agentClassVersionSuffix(version) {
  version = String(version || '').trim();
  return version ? ('@' + version) : '';
}

function _agentClassIssueMessage(issue) {
  if (typeof issue === 'string') return issue;
  issue = issue || {};
  var text = String(issue.message || issue.code || 'Agent Class issue');
  if (issue.path) text += ' (' + issue.path + ')';
  return text;
}

function agentClassManagerReceiveList(msg) {
  msg = msg || {};
  _agentClassList = _agentClassSort(Array.isArray(msg.classes) ? msg.classes : []);
  _agentClassIssues = Array.isArray(msg.issues) ? msg.issues.slice() : [];
  _agentClassLoadedBaseDir = _agentClassLoadingBaseDir || _agentClassCurrentBaseDir();
  _agentClassLoadingBaseDir = null;
  _agentClassPickerLoading = false;
  if (_agentClassSelected && !_agentClassById(_agentClassSelected)) {
    _agentClassSelected = '';
    _agentClassPreview = null;
    _agentClassEditorNew = false;
    _agentClassEditorDirty = false;
  }
  agentClassRenderOpenPickers();
  if (_libraryActiveTab === 'agent_classes') renderAgentClassesPanel();
}

function agentClassManagerReceivePreview(msg) {
  msg = msg || {};
  if (msg.agent_class && msg.agent_class.id) {
    _agentClassPreview = msg.agent_class;
    _agentClassSelected = String(msg.agent_class.id || _agentClassSelected || '').trim();
    _agentClassEditorNew = false;
    _agentClassEditorDirty = false;
    _agentClassValidation = null;
    _agentClassValidationSignature = '';
    _agentClassEditorError = '';
  }
  if (_libraryActiveTab === 'agent_classes') renderAgentClassesPanel();
}

function agentClassManagerReceiveValidation(msg) {
  msg = msg || {};
  var requestId = String(msg.request_id || '').trim();
  if (_agentClassValidationRequestId && requestId && requestId !== _agentClassValidationRequestId) return;
  _agentClassValidationInFlight = false;
  _agentClassValidation = msg;
  _agentClassValidationSignature = _agentClassDraftSignature(_agentClassReadFormSafe());
  _agentClassPreview = msg.agent_class || _agentClassPreview;
  _agentClassEditorError = '';
  _agentClassEditorMessage = msg.valid ? 'Validation passed. Review the normalized preview before saving.' : 'Validation found errors; fix them before saving.';
  if (_libraryActiveTab === 'agent_classes') renderAgentClassesPanel();
}

function agentClassManagerReceiveMutation(msg) {
  msg = msg || {};
  _agentClassValidationInFlight = false;
  _agentClassLastMutationRequestId = '';
  if (Array.isArray(msg.classes)) {
    _agentClassList = _agentClassSort(msg.classes);
    _agentClassIssues = Array.isArray(msg.registry_issues) ? msg.registry_issues.slice() : _agentClassIssues;
  }
  if (msg.agent_class && msg.agent_class.id) {
    _agentClassSelected = String(msg.agent_class.id || '').trim();
    _agentClassPreview = msg.agent_class;
  }
  if (msg.operation === 'deleted' || msg.deleted) {
    var deleted = String(msg.class_id || msg.deleted || (_agentClassPreview && _agentClassPreview.id) || '').trim();
    if (deleted && deleted === _agentClassSelected) {
      _agentClassSelected = '';
      _agentClassPreview = null;
    }
  }
  _agentClassEditorNew = false;
  _agentClassEditorDirty = false;
  _agentClassValidation = null;
  _agentClassValidationSignature = '';
  _agentClassEditorError = msg.ok === false ? (msg.message || 'Agent Class save failed.') : '';
  _agentClassEditorMessage = msg.ok === false ? '' : ('Agent Class ' + (msg.operation || 'saved') + '.');
  agentClassRenderOpenPickers();
  if (_libraryActiveTab === 'agent_classes') renderAgentClassesPanel();
  if (typeof _showToast === 'function') {
    _showToast(_agentClassEditorError || _agentClassEditorMessage, _agentClassEditorError ? 'error' : 'success');
  }
}

function agentClassManagerReceiveLaunchResult(msg) {
  msg = msg || {};
  _agentClassLaunchResult = msg;
  var agent = msg.agent || msg;
  var status = (agent && agent.agent_class_status) || {};
  var classId = status.effective_class_id || (msg.agent_class && msg.agent_class.id) || '';
  var profileStatus = (agent && agent.agent_profile_status) || {};
  var profileId = profileStatus.effective_profile_id || status.next_launch_profile_id || '';
  if (typeof _showToast === 'function' && classId) {
    _showToast('Launched ' + (agent.kind || msg.base_kind || 'agent') + ' with Agent Class ' + classId + (profileId ? (' (internal policy ' + profileId + ')') : '') + '.', 'success');
  }
  if (_libraryActiveTab === 'agent_classes') renderAgentClassesPanel();
}

function agentClassManagerHandleError(msg) {
  var text = String((msg && (msg.message || msg.error)) || '').trim();
  if (!text) return false;
  var maybeClass = /Agent Class|agent class|class_id|invalid_agent_class|agent_class/.test(text + ' ' + String((msg && msg.code) || ''));
  if (!maybeClass && !_agentClassValidationInFlight && !_agentClassLastMutationRequestId) return false;
  _agentClassValidationInFlight = false;
  _agentClassLastMutationRequestId = '';
  _agentClassEditorError = text;
  if (typeof _showToast === 'function') _showToast(text, 'error');
  if (_libraryActiveTab === 'agent_classes') renderAgentClassesPanel();
  return maybeClass;
}

function renderAgentClassesPanel() {
  var panel = document.getElementById('panel-templates');
  if (!panel) return;
  var restoreState = _agentClassSkipNextDraftCapture ? null : _agentClassCaptureEditorUiState();
  _agentClassSkipNextDraftCapture = false;
  if (restoreState && restoreState.form && _agentClassEditorDirty) {
    _agentClassApplyEditorDraft(restoreState.form);
  }
  var baseDir = _agentClassCurrentBaseDir();
  var loaded = _agentClassLoadedBaseDir === baseDir || (_agentClassLoadedBaseDir == null && _agentClassLoadingBaseDir !== baseDir);
  var list = loaded ? _agentClassList : [];
  var selected = loaded ? _agentClassSelected : '';
  var html = '';
  html += '<div class="tpled-header agent-class-header">';
  html += '<div class="tpled-header-copy">';
  html += '<div class="tpled-header-title-row"><span class="tpled-header-title">Agent Classes</span>' + _libraryTabsHtml() + '</div>';
  html += '<div class="tpled-header-subtitle">Agent Classes are the operator-facing objects for authoring, selection, and launch. Internal Agent Profile policy is shown only in Advanced/Internal enforcement details.</div>';
  html += '</div>';
  html += '<div class="tpled-header-controls">';
  html += '<select class="tpled-select" id="agent-class-select" onchange="agentClassManagerSelect(this.value)">';
  html += '<option value="">Select…</option>';
  var builtins = list.filter(function(item) { return item && item.builtin; });
  var customs = list.filter(function(item) { return item && !item.builtin && !_agentClassIsArchived(item); });
  var archived = list.filter(function(item) { return item && !item.builtin && _agentClassIsArchived(item); });
  html += _agentClassSelectGroupHtml('Built-in', builtins, selected);
  html += _agentClassSelectGroupHtml('Project/custom', customs, selected);
  html += _agentClassSelectGroupHtml('Archived/disabled', archived, selected);
  html += '</select>';
  html += '<button class="tpled-new-btn" onclick="agentClassManagerNew()" title="New Agent Class">+</button>';
  html += '<button class="tpled-new-btn" onclick="agentClassManagerLoad(true)" title="Refresh">&#x21BB;</button>';
  html += '</div></div>';
  html += '<div class="tpled-editor agent-class-editor" id="agent-class-editor"></div>';
  panel.innerHTML = html;
  renderAgentClassManagerEditor(restoreState);
}

function _agentClassSelectGroupHtml(label, list, selected) {
  if (!list || !list.length) return '';
  var html = '<optgroup label="' + esc(label) + '">';
  for (var i = 0; i < list.length; i++) {
    var item = list[i] || {};
    var id = String(item.id || '').trim();
    if (!id) continue;
    var optionLabel = _agentClassDisplayName(item, id)
      + _agentClassVersionSuffix(item.version)
      + ' · ' + (item.base_kind || 'agent')
      + ' · ' + _agentClassStatus(item);
    html += '<option value="' + esc(id) + '"' + (selected === id ? ' selected' : '') + '>'
      + esc(optionLabel) + '</option>';
  }
  html += '</optgroup>';
  return html;
}

function agentClassManagerSelect(classId) {
  classId = String(classId || '').trim();
  _agentClassSelected = classId;
  _agentClassPreview = classId ? (_agentClassById(classId) || null) : null;
  _agentClassEditorNew = false;
  _agentClassEditorDirty = false;
  _agentClassValidation = null;
  _agentClassValidationSignature = '';
  _agentClassEditorError = '';
  _agentClassEditorMessage = '';
  _agentClassSkipNextDraftCapture = true;
  renderAgentClassesPanel();
  if (classId) _agentClassSendWithBaseDir({ cmd: 'agent_class_preview', class_id: classId });
}

function agentClassManagerNew(baseKind) {
  var kind = String(baseKind || 'worker').trim();
  if (kind !== 'architect' && kind !== 'engineer' && kind !== 'worker') kind = 'worker';
  _agentClassSelected = '';
  _agentClassPreview = _agentClassDefaultDraft(kind);
  _agentClassEditorNew = true;
  _agentClassEditorDirty = true;
  _agentClassValidation = null;
  _agentClassValidationSignature = '';
  _agentClassEditorMessage = '';
  _agentClassEditorError = '';
  _agentClassSkipNextDraftCapture = true;
  agentClassManagerRequestProfiles(_agentClassCurrentBaseDir(), false);
  renderAgentClassesPanel();
  var inp = document.getElementById('agent-class-id');
  if (inp) inp.focus();
}

function _agentClassDefaultDraft(kind) {
  var profileId = _agentClassDefaultProfileId(kind);
  return {
    id: '',
    version: '1',
    base_kind: kind,
    display_name: '',
    description: '',
    lifecycle: 'stable',
    agent_profile_ref: { id: profileId, version: _agentClassProfileVersion(profileId, '1') },
    agent_profile: _agentClassProfileById(profileId) || {},
    prompt: '',
    metadata: { ui: {} },
    draft: { scratch_only: false, approved_for_live_dogfood: false },
    custom: true,
    source: 'project',
    launchable: true,
    status: 'full',
  };
}

function _agentClassEditablePreview() {
  if (_agentClassEditorNew) return _agentClassPreview || _agentClassDefaultDraft('worker');
  return _agentClassPreview || _agentClassById(_agentClassSelected) || null;
}

function renderAgentClassManagerEditor(restoreState) {
  var el = document.getElementById('agent-class-editor');
  if (!el) return;
  var baseDir = _agentClassCurrentBaseDir();
  var loaded = _agentClassLoadedBaseDir === baseDir || (_agentClassLoadedBaseDir == null && _agentClassLoadingBaseDir !== baseDir);
  if (!loaded) {
    el.innerHTML = '<div class="tpled-empty">Loading Agent Classes…</div>';
    return;
  }
  var html = '';
  html += _agentClassCardsHtml();
  if (_agentClassIssues.length) html += _agentClassIssuesHtml(_agentClassIssues, 'Registry issues');
  if (_agentClassEditorMessage) html += '<div class="agent-class-message">' + esc(_agentClassEditorMessage) + '</div>';
  if (_agentClassEditorError) html += '<div class="agent-class-error">' + esc(_agentClassEditorError) + '</div>';
  var preview = _agentClassEditablePreview();
  if (!preview) {
    html += '<div class="tpled-empty">Pick a class above, or click <b>+</b> to create a trusted project class.</div>';
    el.innerHTML = html;
    _agentClassRestoreEditorUiState(el, restoreState);
    return;
  }
  html += '<div class="agent-class-workspace">';
  html += _agentClassEditorFormHtml(preview);
  html += _agentClassPreviewHtml(preview, _agentClassValidation);
  html += '</div>';
  el.innerHTML = html;
  _agentClassRestoreEditorUiState(el, restoreState);
  el.querySelectorAll('textarea').forEach(_tplAutoResize);
}

function _agentClassCardsHtml() {
  var list = _agentClassSort(_agentClassList);
  if (!list.length) return '<div class="agent-class-card-list agent-class-card-list-empty">No Agent Classes found.</div>';
  var html = '<div class="agent-class-card-list" data-agent-class-card-list>';
  for (var i = 0; i < list.length; i++) {
    var item = list[i] || {};
    var id = String(item.id || '').trim();
    if (!id) continue;
    var active = id === _agentClassSelected;
    var status = _agentClassStatus(item);
    var classes = 'agent-class-card agent-class-status-' + status.replace(/[^a-z0-9_-]/gi, '-').toLowerCase();
    if (active) classes += ' active';
    if (_agentClassIsArchived(item) || item.launchable === false) classes += ' disabled';
    html += '<button type="button" class="' + esc(classes) + '" onclick="agentClassManagerSelect(\'' + esc(id) + '\')">';
    html += '<span class="agent-class-card-title">' + esc(_agentClassDisplayName(item, id)) + '</span>';
    html += '<span class="agent-class-card-id">' + esc(id + _agentClassVersionSuffix(item.version)) + '</span>';
    html += '<span class="agent-class-card-chips">';
    html += '<span>' + esc(item.source || (item.builtin ? 'builtin' : 'project')) + '</span>';
    html += '<span>' + esc(item.base_kind || 'agent') + '</span>';
    html += '<span>' + esc(item.lifecycle || 'stable') + '</span>';
    html += '<span>' + esc(status) + '</span>';
    if (item.scratch_only) html += '<span>scratch</span>';
    if (_agentClassIsArchived(item)) html += '<span>archived</span>';
    html += '</span>';
    if (item.source_path) html += '<span class="agent-class-card-path">' + esc(item.source_path) + '</span>';
    html += '</button>';
  }
  html += '</div>';
  return html;
}

function _agentClassEditorFormHtml(preview) {
  preview = preview || {};
  var isBuiltin = !!preview.builtin;
  var archived = _agentClassIsArchived(preview);
  var readOnly = isBuiltin || archived;
  var ref = preview.agent_profile_ref || {};
  var metadata = preview.metadata && typeof preview.metadata === 'object' ? preview.metadata : {};
  var ui = metadata.ui && typeof metadata.ui === 'object' ? metadata.ui : {};
  var draft = preview.draft && typeof preview.draft === 'object' ? preview.draft : {};
  var title = _agentClassEditorNew ? 'Create project Agent Class' : (isBuiltin ? 'Built-in Agent Class' : (archived ? 'Archived Agent Class' : 'Edit project Agent Class'));
  var html = '<div class="agent-class-form tpled-form">';
  html += '<div class="agent-class-form-head"><div><div class="agent-class-form-title">' + esc(title) + '</div>';
  html += '<div class="agent-class-form-subtitle">Class-first YAML-backed fields only; raw tools, grants, denies, and class-local capability deltas are intentionally not exposed.</div></div></div>';
  if (readOnly) {
    html += '<div class="agent-class-readonly-note">' + esc(isBuiltin ? 'Built-in classes are read-only. Duplicate into a project class to customize.' : 'Archived classes stay visible for audit/preview but cannot be edited or launched here.') + '</div>';
  }
  html += '<label>ID <span class="label-req">*</span></label>';
  html += '<input id="agent-class-id" value="' + esc(preview.id || '') + '" ' + (readOnly || !_agentClassEditorNew ? 'readonly ' : '') + 'oninput="agentClassManagerMarkDirty()" autocomplete="off" placeholder="release-architect">';
  html += '<label>Version</label><input id="agent-class-version" value="' + esc(preview.version || '1') + '" ' + (readOnly ? 'readonly ' : '') + 'oninput="agentClassManagerMarkDirty()" autocomplete="off">';
  html += '<label>Base kind</label><select id="agent-class-base-kind" ' + (readOnly || (!_agentClassEditorNew && preview.id) ? 'disabled ' : '') + 'onchange="agentClassManagerBaseKindChanged()">';
  ['architect', 'engineer', 'worker'].forEach(function(kind) {
    html += '<option value="' + kind + '"' + (String(preview.base_kind || '') === kind ? ' selected' : '') + '>' + kind + '</option>';
  });
  html += '</select>';
  html += '<label>Display name <span class="label-req">*</span></label><input id="agent-class-display-name" value="' + esc(preview.display_name || '') + '" ' + (readOnly ? 'readonly ' : '') + 'oninput="agentClassManagerMarkDirty()" autocomplete="off">';
  html += '<label>Description</label><input id="agent-class-description" value="' + esc(preview.description || '') + '" ' + (readOnly ? 'readonly ' : '') + 'oninput="agentClassManagerMarkDirty()" autocomplete="off">';
  html += '<label>Lifecycle</label><select id="agent-class-lifecycle" ' + (readOnly ? 'disabled ' : '') + 'onchange="agentClassManagerMarkDirty()">';
  ['stable', 'draft', 'experimental'].forEach(function(value) {
    html += '<option value="' + value + '"' + (String(preview.lifecycle || 'stable') === value ? ' selected' : '') + '>' + value + '</option>';
  });
  html += '</select>';
  html += '<details class="tpled-section" open><summary>Class instructions</summary>';
  html += '<label>Additive prompt/class instructions</label><textarea id="agent-class-prompt" rows="6" ' + (readOnly ? 'readonly ' : '') + 'oninput="_tplAutoResize(this);agentClassManagerMarkDirty()" placeholder="Optional additive context appended after the base-kind prompt.">' + esc(preview.prompt || '') + '</textarea>';
  html += '</details>';
  html += '<details class="tpled-section agent-class-internal-policy-section" id="agent-class-internal-policy-section"><summary>Advanced/Internal enforcement policy</summary>';
  html += '<div class="agent-class-hint">Agent Profile is the generated/internal MCP-capability enforcement detail. Normal operators select Agent Classes; use this only when authoring or troubleshooting policy pairing.</div>';
  html += '<label>Internal Agent Profile</label><select id="agent-class-profile-id" ' + (readOnly ? 'disabled ' : '') + 'onchange="agentClassManagerProfileChanged()">' + _agentClassProfileOptionsHtml(preview.base_kind, ref.id) + '</select>';
  html += '<input id="agent-class-profile-version" value="' + esc(ref.version || _agentClassProfileVersion(ref.id, '')) + '" ' + (readOnly ? 'readonly ' : '') + 'oninput="agentClassManagerMarkDirty()" autocomplete="off" placeholder="profile version">';
  if (_agentClassProfileLoadingBaseDir) html += '<div class="agent-class-hint">Loading internal Agent Profiles…</div>';
  if (_agentClassProfileIssues.length) html += _agentClassIssuesHtml(_agentClassProfileIssues.slice(0, 3), 'Internal profile registry issues');
  html += '</details>';
  html += '<details class="tpled-section"><summary>UI metadata</summary>';
  html += '<label>Label</label><input id="agent-class-ui-label" value="' + esc(ui.label || '') + '" ' + (readOnly ? 'readonly ' : '') + 'oninput="agentClassManagerMarkDirty()" autocomplete="off">';
  html += '<label>Icon</label><input id="agent-class-ui-icon" value="' + esc(ui.icon || '') + '" ' + (readOnly ? 'readonly ' : '') + 'oninput="agentClassManagerMarkDirty()" autocomplete="off" placeholder="emoji or symbol">';
  html += '<label>Badge</label><input id="agent-class-ui-badge" value="' + esc(ui.badge || '') + '" ' + (readOnly ? 'readonly ' : '') + 'oninput="agentClassManagerMarkDirty()" autocomplete="off">';
  html += '<label>Color</label><input id="agent-class-ui-color" value="' + esc(ui.color || '') + '" ' + (readOnly ? 'readonly ' : '') + 'oninput="agentClassManagerMarkDirty()" autocomplete="off" placeholder="#hex">';
  html += '<label>Archetype</label><input id="agent-class-archetype" value="' + esc(metadata.archetype || '') + '" ' + (readOnly ? 'readonly ' : '') + 'oninput="agentClassManagerMarkDirty()" autocomplete="off" placeholder="product_manager">';
  html += '</details>';
  html += '<details class="tpled-section"><summary>Draft / scratch marker</summary>';
  html += '<label class="gs-checkbox"><input id="agent-class-scratch-only" type="checkbox" ' + (draft.scratch_only ? 'checked ' : '') + (readOnly ? 'disabled ' : '') + 'onchange="agentClassManagerMarkDirty()"> Scratch-only draft class</label>';
  html += '<div class="agent-class-hint">Draft classes must be scratch-only and must not claim live dogfood approval.</div>';
  html += '</details>';
  html += '<div class="tpled-actions agent-class-actions">';
  if (!readOnly) {
    html += '<button class="btn-cancel" onclick="agentClassManagerValidate()"' + (_agentClassValidationInFlight ? ' disabled' : '') + '>' + (_agentClassValidationInFlight ? 'Validating…' : 'Validate') + '</button>';
    var saveDisabled = _agentClassSaveDisabledReason();
    html += '<button class="btn-primary" onclick="agentClassManagerSave()"' + (saveDisabled ? ' disabled title="' + esc(saveDisabled) + '"' : '') + '>Save</button>';
    if (saveDisabled) html += '<span class="agent-class-disabled-reason">' + esc(saveDisabled) + '</span>';
  }
  if (preview.custom && !_agentClassEditorNew && !archived) {
    html += '<button class="btn-cancel" onclick="agentClassManagerArchive()">Archive/disable</button>';
  }
  if (preview.custom && !_agentClassEditorNew) {
    html += '<button class="btn-cancel btn-danger" onclick="agentClassManagerDelete()">Delete</button>';
  }
  html += '<button class="btn-cancel" onclick="agentClassManagerDuplicate()">Duplicate</button>';
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentClassProfileOptionsHtml(kind, selectedId) {
  kind = String(kind || 'worker').trim();
  selectedId = String(selectedId || '').trim() || _agentClassDefaultProfileId(kind);
  var profiles = _agentClassCompatibleProfiles(kind);
  var html = '';
  if (!profiles.length) {
    var fallback = selectedId || _agentClassDefaultProfileId(kind);
    html += '<option value="' + esc(fallback) + '" selected>' + esc(fallback || 'No compatible profiles loaded') + '</option>';
    return html;
  }
  for (var i = 0; i < profiles.length; i++) {
    var profile = profiles[i] || {};
    var id = String(profile.id || '').trim();
    if (!id) continue;
    var label = String(profile.display_name || id) + _agentClassVersionSuffix(profile.version) + ' · ' + (profile.status || profile.lifecycle || 'full');
    html += '<option value="' + esc(id) + '"' + (selectedId === id ? ' selected' : '') + '>' + esc(label) + '</option>';
  }
  return html;
}

function _agentClassPreviewHtml(preview, validation) {
  preview = preview || {};
  var status = _agentClassStatus(preview);
  var ref = preview.agent_profile_ref || {};
  var profile = preview.agent_profile || _agentClassProfileById(ref.id) || {};
  var disabledReason = _agentClassLaunchDisabledReason(preview, preview.base_kind);
  var primaryLabel = String(preview.primary_identity_label || preview.primary_display_name || _agentClassDisplayName(preview, preview.id || 'Agent Class')).trim();
  var secondaryLabel = String(preview.secondary_base_kind_label
    || (preview.secondary_base_kind_metadata && preview.secondary_base_kind_metadata.base_kind_label)
    || preview.base_kind
    || 'agent').trim();
  var html = '<div class="agent-class-preview agent-class-preview-' + esc(status.replace(/[^a-z0-9_-]/gi, '-').toLowerCase()) + '">';
  html += '<div class="agent-class-preview-head"><div><div class="agent-class-preview-title">' + esc(primaryLabel || 'Agent Class') + esc(_agentClassVersionSuffix(preview.version)) + '</div>';
  html += '<div class="agent-class-preview-subtitle">' + esc((preview.id || 'unsaved') + ' · ' + secondaryLabel + ' base metadata') + '</div></div>';
  html += '<div class="agent-class-preview-chips">';
  html += '<span>' + esc(preview.source || (preview.builtin ? 'builtin' : 'project')) + '</span>';
  html += '<span>' + esc(secondaryLabel) + '</span>';
  html += '<span>' + esc(preview.lifecycle || 'stable') + '</span>';
  html += '<span>' + esc(status) + '</span>';
  if (preview.scratch_only || (preview.draft && preview.draft.scratch_only)) html += '<span>scratch-only</span>';
  if (_agentClassIsArchived(preview)) html += '<span>archived</span>';
  if (preview.external_connector_caveat) html += '<span>external connector caveat</span>';
  html += '</div></div>';
  if (preview.description) html += '<div class="agent-class-preview-description">' + esc(preview.description) + '</div>';
  var prompt = preview.prompt_summary || {};
  html += '<div class="agent-class-summary-grid">';
  html += '<div><span>Primary identity</span><strong>' + esc(primaryLabel || '—') + '</strong></div>';
  html += '<div><span>Secondary/base metadata</span><strong>' + esc(secondaryLabel || '—') + '</strong></div>';
  html += '<div><span>Prompt</span><strong>' + esc(prompt.has_prompt ? ((prompt.char_count || 0) + ' chars') : 'No class prompt') + '</strong></div>';
  html += '<div><span>Launchable</span><strong>' + esc(disabledReason ? 'No' : 'Yes') + '</strong></div>';
  html += '</div>';
  if (prompt.preview) html += '<div class="agent-class-prompt-preview">' + esc(prompt.preview) + '</div>';
  var issues = [];
  if (validation && Array.isArray(validation.errors)) issues = issues.concat(validation.errors);
  if (validation && Array.isArray(validation.warnings)) issues = issues.concat(validation.warnings);
  if (Array.isArray(preview.warnings)) issues = issues.concat(preview.warnings);
  if (issues.length) html += _agentClassIssuesHtml(issues, 'Warnings / validation');
  if (disabledReason) html += '<div class="agent-class-error">' + esc(disabledReason) + '</div>';
  if (Array.isArray(preview.restrictions) && preview.restrictions.length) {
    html += '<div class="agent-class-restrictions"><div class="agent-class-block-title">Restrictions</div><ul>';
    for (var i = 0; i < preview.restrictions.length; i++) html += '<li>' + esc(preview.restrictions[i]) + '</li>';
    html += '</ul></div>';
  }
  if (preview.external_connector_caveat) html += '<div class="agent-class-caveat">' + esc(preview.external_connector_caveat) + '</div>';
  if (preview.source_path) html += '<div class="agent-class-storage">Source: <code>' + esc(preview.source_path) + '</code></div>';
  if (validation && validation.normalized) {
    html += '<details class="agent-class-normalized"><summary>Normalized preview</summary><pre>' + esc(JSON.stringify(validation.normalized, null, 2)) + '</pre></details>';
  }
  html += _agentClassInternalPolicyPreviewHtml(preview, profile, ref);
  html += _agentClassLaunchBoxHtml(preview, disabledReason);
  html += '</div>';
  return html;
}

function _agentClassInternalPolicyPreviewHtml(preview, profile, ref) {
  preview = preview || {};
  profile = profile || {};
  ref = ref || {};
  var policy = preview.internal_policy && typeof preview.internal_policy === 'object' ? preview.internal_policy : {};
  var html = '<details class="agent-class-normalized agent-class-internal-policy-preview"><summary>Advanced/Internal enforcement details</summary>';
  html += '<div class="agent-class-pairing"><div><span>Internal Agent Profile</span><strong>'
    + esc((ref.id || profile.id || '—') + _agentClassVersionSuffix(ref.version || profile.version))
    + '</strong></div>';
  html += '<div><span>Profile status</span><strong>' + esc(profile.status || profile.lifecycle || '—') + '</strong></div>';
  if (profile.capability_count != null) html += '<div><span>Profile capabilities</span><strong>' + esc(profile.capability_count) + '</strong></div>';
  html += '<div><span>Runtime enforcement</span><strong>' + esc(preview.runtime_enforcement || 'launch_frozen_agent_class_profile_pairing') + '</strong></div>';
  if (policy.mode) html += '<div><span>Policy mode</span><strong>' + esc(policy.mode) + '</strong></div>';
  if (policy.profile_source) html += '<div><span>Policy source</span><strong>' + esc(policy.profile_source) + '</strong></div>';
  if (policy.generated_profile_written_to_project_yaml !== undefined) {
    html += '<div><span>Generated profile YAML</span><strong>'
      + esc(policy.generated_profile_written_to_project_yaml ? 'yes' : 'no')
      + '</strong></div>';
  }
  html += '</div></details>';
  return html;
}

function _agentClassLaunchBoxHtml(preview, disabledReason) {
  if (!preview || !preview.id || _agentClassEditorNew) return '';
  var classId = String(preview.id || '');
  var draft = _agentClassLaunchDrafts[classId] || {};
  var group = draft.group || (typeof _currentGroup === 'function' ? _currentGroup() : '') || '';
  var name = draft.name || _agentClassDisplayName(preview, classId);
  var html = '<div class="agent-class-launch-box">';
  html += '<div class="agent-class-block-title">Launch new ' + esc(preview.base_kind || 'agent') + ' from this class</div>';
  html += '<label>Name</label><input id="agent-class-launch-name" value="' + esc(name) + '" oninput="agentClassManagerLaunchDraftChanged()" autocomplete="off">';
  html += '<label>Group</label><select id="agent-class-launch-group" onchange="agentClassManagerLaunchDraftChanged()">';
  var groups = state && state.groups ? Object.keys(state.groups) : [];
  if (!groups.length && group) groups = [group];
  for (var i = 0; i < groups.length; i++) {
    var g = groups[i];
    html += '<option value="' + esc(g) + '"' + (g === group ? ' selected' : '') + '>' + esc(g) + '</option>';
  }
  html += '</select>';
  html += '<button class="btn-primary" onclick="agentClassManagerLaunchSelected()"' + (disabledReason ? ' disabled title="' + esc(disabledReason) + '"' : '') + '>Launch from class</button>';
  if (_agentClassLaunchResult) html += _agentClassLaunchResultHtml(_agentClassLaunchResult);
  html += '</div>';
  return html;
}

function _agentClassLaunchResultHtml(msg) {
  msg = msg || {};
  var agent = msg.agent || msg;
  var classStatus = (agent && agent.agent_class_status) || {};
  var profileStatus = (agent && agent.agent_profile_status) || {};
  if (!classStatus.effective_class_id && !profileStatus.effective_profile_id) return '';
  var html = '<div class="agent-class-launch-result">';
  html += '<div><span>Launched</span><strong>' + esc((agent.kind || msg.base_kind || 'agent') + ' ' + (agent.name || agent.id || '')) + '</strong></div>';
  html += '<div><span>Frozen class</span><strong>' + esc((classStatus.effective_class_id || '—') + _agentClassVersionSuffix(classStatus.effective_class_version)) + '</strong></div>';
  html += '<div><span>Frozen internal policy</span><strong>' + esc((profileStatus.effective_profile_id || classStatus.next_launch_profile_id || '—') + _agentClassVersionSuffix(profileStatus.effective_profile_version)) + '</strong></div>';
  html += '</div>';
  return html;
}

function _agentClassIssuesHtml(issues, title) {
  issues = issues || [];
  if (!issues.length) return '';
  var html = '<div class="agent-class-issues"><div class="agent-class-block-title">' + esc(title || 'Issues') + '</div><ul>';
  for (var i = 0; i < Math.min(issues.length, 8); i++) {
    html += '<li>' + esc(_agentClassIssueMessage(issues[i])) + '</li>';
  }
  if (issues.length > 8) html += '<li>+' + (issues.length - 8) + ' more</li>';
  html += '</ul></div>';
  return html;
}

function agentClassManagerMarkDirty() {
  _agentClassEditorDirty = true;
  _agentClassEditorMessage = '';
  _agentClassEditorError = '';
}

function agentClassManagerBaseKindChanged() {
  var form = _agentClassReadFormSafe();
  var kind = form.base_kind || 'worker';
  var profileId = _agentClassDefaultProfileId(kind);
  form.agent_profile_ref = { id: profileId, version: _agentClassProfileVersion(profileId, '1') };
  _agentClassPreview = Object.assign(_agentClassDefaultDraft(kind), form);
  _agentClassEditorDirty = true;
  _agentClassValidation = null;
  _agentClassValidationSignature = '';
  _agentClassSkipNextDraftCapture = true;
  renderAgentClassesPanel();
}

function agentClassManagerProfileChanged() {
  var sel = document.getElementById('agent-class-profile-id');
  var version = document.getElementById('agent-class-profile-version');
  if (sel && version) version.value = _agentClassProfileVersion(sel.value, version.value || '');
  agentClassManagerMarkDirty();
}

function _agentClassReadFormSafe() {
  function value(id, fallback) {
    var el = document.getElementById(id);
    if (!el || !('value' in el)) return fallback || '';
    return el.value;
  }
  function checked(id) {
    var el = document.getElementById(id);
    return !!(el && el.checked);
  }
  var kind = String(value('agent-class-base-kind', (_agentClassPreview && _agentClassPreview.base_kind) || 'worker')).trim();
  var profileId = String(value('agent-class-profile-id', _agentClassDefaultProfileId(kind))).trim();
  var profileVersion = String(value('agent-class-profile-version', _agentClassProfileVersion(profileId, ''))).trim();
  var lifecycle = String(value('agent-class-lifecycle', 'stable')).trim() || 'stable';
  var data = {
    id: String(value('agent-class-id', '')).trim(),
    version: String(value('agent-class-version', '1')).trim() || '1',
    base_kind: kind,
    display_name: String(value('agent-class-display-name', '')).trim(),
    description: String(value('agent-class-description', '')).trim(),
    lifecycle: lifecycle,
    agent_profile_ref: { id: profileId, version: profileVersion },
    prompt: value('agent-class-prompt', ''),
  };
  var ui = {};
  ['label', 'icon', 'badge', 'color'].forEach(function(key) {
    var v = String(value('agent-class-ui-' + key, '')).trim();
    if (v) ui[key] = v;
  });
  var metadata = {};
  var archetype = String(value('agent-class-archetype', '')).trim();
  if (archetype) metadata.archetype = archetype;
  if (Object.keys(ui).length) metadata.ui = ui;
  if (Object.keys(metadata).length) data.metadata = metadata;
  if (checked('agent-class-scratch-only') || lifecycle === 'draft') {
    data.draft = {
      scratch_only: checked('agent-class-scratch-only'),
      approved_for_live_dogfood: false,
    };
  }
  return data;
}

function _agentClassDraftSignature(data) {
  try { return JSON.stringify(data || _agentClassReadFormSafe()); }
  catch (_e) { return ''; }
}

function _agentClassValidationCurrent() {
  if (!_agentClassValidation || !_agentClassValidation.valid) return false;
  return _agentClassValidationSignature === _agentClassDraftSignature(_agentClassReadFormSafe());
}

function _agentClassSaveDisabledReason() {
  if (_agentClassValidationInFlight) return 'Validation is still running.';
  if (!_agentClassEditorDirty && !_agentClassEditorNew) return 'No changes to save.';
  if (!_agentClassValidation) return 'Validate before saving.';
  if (!_agentClassValidation.valid) return 'Fix validation errors before saving.';
  if (!_agentClassValidationCurrent()) return 'Validate the latest draft before saving.';
  return '';
}

function agentClassManagerValidate() {
  var data = _agentClassReadFormSafe();
  if (!data.id) {
    var idEl = document.getElementById('agent-class-id');
    if (idEl) idEl.focus();
  }
  _agentClassValidationInFlight = true;
  _agentClassValidationRequestId = 'agent-class-' + Date.now();
  _agentClassValidationSignature = _agentClassDraftSignature(data);
  _agentClassEditorError = '';
  _agentClassEditorMessage = 'Validating Agent Class…';
  _agentClassSendWithBaseDir({
    cmd: 'agent_class_validate',
    request_id: _agentClassValidationRequestId,
    agent_class: data,
  });
  renderAgentClassesPanel();
}

function agentClassManagerSave() {
  var disabled = _agentClassSaveDisabledReason();
  if (disabled) {
    _agentClassEditorError = disabled;
    renderAgentClassesPanel();
    return;
  }
  var data = _agentClassReadFormSafe();
  _agentClassLastMutationRequestId = 'agent-class-save-' + Date.now();
  var cmd = _agentClassEditorNew ? 'agent_class_create' : 'agent_class_update';
  _agentClassSendWithBaseDir({
    cmd: cmd,
    request_id: _agentClassLastMutationRequestId,
    agent_class: data,
  });
  _agentClassEditorMessage = 'Saving Agent Class…';
  renderAgentClassesPanel();
}

function agentClassManagerArchive() {
  var item = _agentClassEditablePreview();
  if (!item || !item.id || !item.custom) return;
  showConfirm('Archive/disable Agent Class "' + item.id + '"? It will stay visible but cannot launch.').then(function(yes) {
    if (!yes) return;
    _agentClassLastMutationRequestId = 'agent-class-archive-' + Date.now();
    _agentClassSendWithBaseDir({ cmd: 'agent_class_archive', request_id: _agentClassLastMutationRequestId, class_id: item.id });
  });
}

function agentClassManagerDelete() {
  var item = _agentClassEditablePreview();
  if (!item || !item.id || !item.custom) return;
  showConfirm('Delete custom Agent Class "' + item.id + '" from project YAML?').then(function(yes) {
    if (!yes) return;
    _agentClassLastMutationRequestId = 'agent-class-delete-' + Date.now();
    _agentClassSendWithBaseDir({ cmd: 'agent_class_delete', request_id: _agentClassLastMutationRequestId, class_id: item.id });
  });
}

function agentClassManagerDuplicate() {
  var src = _agentClassReadFormSafe();
  if (!_agentClassEditorDirty && _agentClassPreview) {
    var ref = _agentClassPreview.agent_profile_ref || {};
    src = {
      id: _agentClassPreview.id || '',
      version: _agentClassPreview.version || '1',
      base_kind: _agentClassPreview.base_kind || 'worker',
      display_name: _agentClassPreview.display_name || '',
      description: _agentClassPreview.description || '',
      lifecycle: _agentClassPreview.lifecycle || 'stable',
      agent_profile_ref: { id: ref.id || _agentClassDefaultProfileId(_agentClassPreview.base_kind), version: ref.version || '' },
      prompt: _agentClassPreview.prompt || '',
      metadata: _agentClassPreview.metadata || {},
      draft: _agentClassPreview.draft || {},
    };
  }
  src.id = (src.id || 'agent-class') + '-copy';
  src.display_name = (src.display_name || 'Agent Class') + ' Copy';
  _agentClassSelected = '';
  _agentClassPreview = src;
  _agentClassEditorNew = true;
  _agentClassEditorDirty = true;
  _agentClassValidation = null;
  _agentClassValidationSignature = '';
  _agentClassSkipNextDraftCapture = true;
  renderAgentClassesPanel();
  var inp = document.getElementById('agent-class-id');
  if (inp) { inp.focus(); inp.select(); }
}

function agentClassManagerLaunchDraftChanged() {
  var item = _agentClassEditablePreview();
  if (!item || !item.id) return;
  _agentClassLaunchDrafts[item.id] = {
    name: (document.getElementById('agent-class-launch-name') || {}).value || '',
    group: (document.getElementById('agent-class-launch-group') || {}).value || '',
  };
}

function agentClassManagerLaunchSelected() {
  var item = _agentClassEditablePreview();
  if (!item || !item.id) return;
  var disabled = _agentClassLaunchDisabledReason(item, item.base_kind);
  if (disabled) {
    _agentClassEditorError = disabled;
    renderAgentClassesPanel();
    return;
  }
  agentClassManagerLaunchDraftChanged();
  var draft = _agentClassLaunchDrafts[item.id] || {};
  var name = String(draft.name || _agentClassDisplayName(item, item.id)).trim();
  var group = String(draft.group || (typeof _currentGroup === 'function' ? _currentGroup() : '') || '').trim();
  if (!name) {
    var nameEl = document.getElementById('agent-class-launch-name');
    if (nameEl) nameEl.focus();
    return;
  }
  _agentClassSendWithBaseDir({
    cmd: 'create_agent_from_class',
    class_id: item.id,
    kind: item.base_kind,
    name: name,
    group: group,
  });
}

function _agentClassCaptureEditorUiState() {
  var snapshot = { form: null, focus: null, scrollTop: null, detailsOpen: {} };
  var root = document.getElementById('agent-class-editor') || document.getElementById('panel-templates');
  if (root && typeof root.scrollTop === 'number') snapshot.scrollTop = root.scrollTop;
  if (root && typeof root.querySelectorAll === 'function') {
    var details = root.querySelectorAll('details[id]') || [];
    for (var d = 0; d < details.length; d++) {
      if (details[d] && details[d].id) snapshot.detailsOpen[details[d].id] = !!details[d].open;
    }
  }
  var formIds = [
    'agent-class-id', 'agent-class-version', 'agent-class-base-kind', 'agent-class-display-name',
    'agent-class-description', 'agent-class-lifecycle', 'agent-class-profile-id', 'agent-class-profile-version',
    'agent-class-prompt', 'agent-class-ui-label', 'agent-class-ui-icon', 'agent-class-ui-badge',
    'agent-class-ui-color', 'agent-class-archetype', 'agent-class-scratch-only',
    'agent-class-launch-name', 'agent-class-launch-group'
  ];
  var form = {};
  var hasForm = false;
  for (var i = 0; i < formIds.length; i++) {
    var el = document.getElementById(formIds[i]);
    if (!el) continue;
    hasForm = true;
    var isCheckbox = String(el.type || '').toLowerCase() === 'checkbox' || formIds[i] === 'agent-class-scratch-only';
    form[formIds[i]] = isCheckbox ? !!el.checked : (('value' in el) ? el.value : '');
  }
  snapshot.form = hasForm ? form : null;
  var active = document.activeElement;
  if (active && root && typeof root.contains === 'function' && root.contains(active)) {
    snapshot.focus = {
      id: active.id || '',
      value: ('value' in active) ? active.value : null,
      checked: ('checked' in active) ? !!active.checked : null,
      selectionStart: typeof active.selectionStart === 'number' ? active.selectionStart : null,
      selectionEnd: typeof active.selectionEnd === 'number' ? active.selectionEnd : null,
      scrollTop: typeof active.scrollTop === 'number' ? active.scrollTop : null,
    };
  }
  return snapshot;
}

function _agentClassApplyEditorDraft(form) {
  if (!form) return;
  // The next render reads the durable preview object, so fold focused draft
  // values back into it before replacing HTML.
  _agentClassPreview = Object.assign(_agentClassPreview || {}, _agentClassFormObjectFromSnapshot(form));
}

function _agentClassFormObjectFromSnapshot(form) {
  form = form || {};
  var kind = form['agent-class-base-kind'] || 'worker';
  var profileId = form['agent-class-profile-id'] || _agentClassDefaultProfileId(kind);
  var out = {
    id: form['agent-class-id'] || '',
    version: form['agent-class-version'] || '1',
    base_kind: kind,
    display_name: form['agent-class-display-name'] || '',
    description: form['agent-class-description'] || '',
    lifecycle: form['agent-class-lifecycle'] || 'stable',
    agent_profile_ref: {
      id: profileId,
      version: form['agent-class-profile-version'] || _agentClassProfileVersion(profileId, ''),
    },
    prompt: form['agent-class-prompt'] || '',
    draft: { scratch_only: !!form['agent-class-scratch-only'], approved_for_live_dogfood: false },
    metadata: { ui: {} },
  };
  ['label', 'icon', 'badge', 'color'].forEach(function(key) {
    var value = form['agent-class-ui-' + key] || '';
    if (value) out.metadata.ui[key] = value;
  });
  if (form['agent-class-archetype']) out.metadata.archetype = form['agent-class-archetype'];
  if (!Object.keys(out.metadata.ui).length) delete out.metadata.ui;
  if (!Object.keys(out.metadata).length) delete out.metadata;
  return out;
}

function _agentClassRestoreEditorUiState(root, snapshot) {
  if (!root || !snapshot) return;
  if (snapshot.detailsOpen && typeof snapshot.detailsOpen === 'object') {
    Object.keys(snapshot.detailsOpen).forEach(function(id) {
      var detail = document.getElementById(id);
      if (detail && 'open' in detail) detail.open = !!snapshot.detailsOpen[id];
    });
  }
  var form = snapshot.form || null;
  if (form) {
    Object.keys(form).forEach(function(id) {
      var el = document.getElementById(id);
      if (!el) return;
      var isCheckbox = String(el.type || '').toLowerCase() === 'checkbox' || id === 'agent-class-scratch-only';
      if (isCheckbox) el.checked = !!form[id];
      else if ('value' in el) el.value = form[id];
    });
  }
  if (typeof snapshot.scrollTop === 'number' && typeof root.scrollTop === 'number') root.scrollTop = snapshot.scrollTop;
  if (!snapshot.focus || !snapshot.focus.id) return;
  var focusEl = document.getElementById(snapshot.focus.id);
  if (!focusEl) return;
  if (snapshot.focus.value != null && 'value' in focusEl && focusEl.value !== snapshot.focus.value) focusEl.value = snapshot.focus.value;
  if (snapshot.focus.checked != null && 'checked' in focusEl) focusEl.checked = !!snapshot.focus.checked;
  if (typeof focusEl.focus === 'function') {
    try { focusEl.focus({ preventScroll: true }); }
    catch (_e) { focusEl.focus(); }
  }
  if (typeof snapshot.focus.selectionStart === 'number' && 'selectionStart' in focusEl) focusEl.selectionStart = snapshot.focus.selectionStart;
  if (typeof snapshot.focus.selectionEnd === 'number' && 'selectionEnd' in focusEl) focusEl.selectionEnd = snapshot.focus.selectionEnd;
  if (typeof snapshot.focus.scrollTop === 'number' && typeof focusEl.scrollTop === 'number') focusEl.scrollTop = snapshot.focus.scrollTop;
}

/* Agent Class launch picker helpers used by add Architect/Engineer/Worker modals. */

function agentClassPickerPrepare(kind, group, baseDir, contextKey) {
  contextKey = String(contextKey || kind || 'agent-class-picker');
  _agentClassPickerContexts[contextKey] = { kind: kind, group: group || '', baseDir: baseDir || '' };
  var resolvedBaseDir = String(baseDir || agentClassBaseDirForGroup(group) || '').trim();
  _agentClassPickerRequestedBaseDir = resolvedBaseDir;
  if (!_agentClassLoadedBaseDir || _agentClassLoadedBaseDir !== resolvedBaseDir) {
    _agentClassPickerLoading = true;
    _agentClassSendWithBaseDir({ cmd: 'agent_class_list' }, resolvedBaseDir);
  }
  agentClassRenderOpenPickers();
}

function agentClassPickerSelected(contextKey) {
  var state = agentClassPickerSelectionState(contextKey);
  return state.ok && !state.defaultSelected ? state.selectedId : '';
}

function agentClassPickerSelectionState(contextKey) {
  contextKey = String(contextKey || '').trim();
  var selected = String(_agentClassPickerSelections[contextKey] || '').trim();
  var ctx = _agentClassPickerContexts[contextKey] || {};
  var kind = String(ctx.kind || '').trim();
  if (!selected) {
    return {
      ok: true,
      defaultSelected: true,
      selectedId: '',
      kind: kind,
      reason: '',
      item: null,
    };
  }
  var item = _agentClassById(selected);
  if (!item) {
    return {
      ok: false,
      defaultSelected: false,
      selectedId: selected,
      kind: kind,
      reason: 'Selected Agent Class is no longer available. Choose another class or Default (no explicit Agent Class).',
      item: null,
    };
  }
  var reason = _agentClassLaunchDisabledReason(item, kind || item.base_kind);
  return {
    ok: !reason,
    defaultSelected: false,
    selectedId: selected,
    kind: kind || String(item.base_kind || ''),
    reason: reason || '',
    item: item,
  };
}

function agentClassPickerSubmitSelection(contextKey) {
  var state = agentClassPickerSelectionState(contextKey);
  if (state.ok) return state;
  agentClassRenderOpenPickers();
  if (typeof _showToast === 'function') {
    _showToast(state.reason || 'Choose a launchable Agent Class or Default (no explicit Agent Class).', 'error');
  }
  return null;
}

function agentClassPickerSelect(contextKey, value) {
  contextKey = String(contextKey || '').trim();
  _agentClassPickerSelections[contextKey] = String(value || '').trim();
  agentClassRenderOpenPickers();
}

function _agentClassPickerCompatible(kind) {
  kind = String(kind || '').trim();
  return _agentClassSort(_agentClassList).filter(function(item) {
    return item && String(item.base_kind || '') === kind;
  });
}

function _agentClassPickerOptionHtml(kind, selected, state) {
  var defaultLabel = 'Default (no explicit Agent Class)';
  var html = '<option value=""' + (!selected ? ' selected' : '') + '>' + esc(defaultLabel) + '</option>';
  var list = _agentClassPickerCompatible(kind);
  var sawSelected = false;
  for (var i = 0; i < list.length; i++) {
    var item = list[i] || {};
    var id = String(item.id || '').trim();
    if (!id) continue;
    if (selected === id) sawSelected = true;
    var reason = _agentClassLaunchDisabledReason(item, kind);
    var label = _agentClassDisplayName(item, id) + _agentClassVersionSuffix(item.version)
      + ' · ' + (item.source || (item.builtin ? 'builtin' : 'project'))
      + ' · ' + _agentClassStatus(item);
    if (reason) label += ' (disabled)';
    html += '<option value="' + esc(id) + '"' + (selected === id ? ' selected' : '') + (reason ? ' disabled' : '') + '>' + esc(label) + '</option>';
  }
  if (selected && !sawSelected) {
    var staleReason = state && state.reason ? (' — ' + state.reason) : '';
    html += '<option value="' + esc(selected) + '" selected disabled>Previously selected: ' + esc(selected + staleReason) + '</option>';
  }
  return html;
}

function _agentClassPickerHint(kind, state) {
  if (!state || typeof state !== 'object') state = agentClassPickerSelectionState('');
  var selected = String(state.selectedId || '').trim();
  if (_agentClassPickerLoading) return 'Loading Agent Classes…';
  if (!selected) return 'No class selected: existing default launch behavior is preserved and Torque freezes default-' + kind + ' at launch.';
  var item = state.item || _agentClassById(selected);
  if (!item) return state.reason || 'Selected Agent Class is not loaded yet.';
  var reason = state.reason || _agentClassLaunchDisabledReason(item, kind);
  var ref = item.agent_profile_ref || {};
  if (reason) return reason;
  return 'Launch freezes ' + _agentClassDisplayName(item, selected) + _agentClassVersionSuffix(item.version)
    + ' as the primary Agent Class identity'
    + (ref.id ? (' with internal policy ' + ref.id + _agentClassVersionSuffix(ref.version)) : '')
    + '.';
}

function _agentClassRenderPicker(rowId, selectId, hintId, kind, contextKey) {
  var row = document.getElementById(rowId);
  var select = document.getElementById(selectId);
  var hint = document.getElementById(hintId);
  if (!row || !select) return;
  row.classList.remove('hidden');
  var state = agentClassPickerSelectionState(contextKey);
  var selected = state.selectedId || '';
  select.innerHTML = _agentClassPickerOptionHtml(kind, selected, state);
  select.value = selected;
  select.onchange = function() { agentClassPickerSelect(contextKey, select.value); };
  select.classList.toggle('invalid', !state.ok);
  if (hint) hint.textContent = _agentClassPickerHint(kind, state);
}

function agentClassRenderOpenPickers() {
  Object.keys(_agentClassPickerContexts).forEach(function(key) {
    var ctx = _agentClassPickerContexts[key] || {};
    if (key === 'add-worker') _agentClassRenderPicker('add-agent-class-row', 'add-agent-class-select', 'add-agent-class-hint', 'worker', key);
    else if (key === 'add-engineer') _agentClassRenderPicker('engineer-agent-class-row', 'engineer-agent-class-select', 'engineer-agent-class-hint', 'engineer', key);
    else if (key === 'add-architect') _agentClassRenderPicker('architect-agent-class-row', 'architect-agent-class-select', 'architect-agent-class-hint', 'architect', key);
    else if (key === 'engineer-launch') _agentClassRenderPicker('engineer-launch-agent-class-row', 'engineer-launch-agent-class-select', 'engineer-launch-agent-class-hint', 'engineer', key);
    else if (ctx.rowId) _agentClassRenderPicker(ctx.rowId, ctx.selectId, ctx.hintId, ctx.kind, key);
  });
}

/* ------------------------------------------------------------------ */
/* Templates panel app — template editor in the taskbar panel          */
/* ------------------------------------------------------------------ */

var _tplEditorList = [];       // cached template list
var _tplEditorSelected = '';   // selected template name
var _tplEditorData = null;     // loaded template data (parsed dict)
var _tplEditorDirty = false;
var _tplEditorNew = false;     // true when creating a new template
var _tplEditorScope = 'project'; // 'project' or 'user'
var _tplPanelView = 'editor';  // 'editor' or 'pipelines'
var _tplPipelinesData = null;  // cached pipeline discovery result

/* ---- Load & render ------------------------------------------------- */

function tplEditorLoad() {
  var group = _currentGroup();
  send({ cmd: 'list_templates', group: group });
}

function _tplKey(t) {
  return (t.global ? 'user:' : 'project:') + t.name;
}

function _tplSelectedName() {
  // Extract name from "scope:name" key
  var idx = _tplEditorSelected.indexOf(':');
  return idx >= 0 ? _tplEditorSelected.slice(idx + 1) : _tplEditorSelected;
}

function tplEditorReceiveList(msg) {
  _tplEditorList = msg.templates || [];

  // If we just saved, select it with the right scope key
  if (msg.saved) {
    var match = _tplEditorList.find(function(t) { return t.name === msg.saved; });
    if (match) _tplEditorSelected = _tplKey(match);
  }
  if (msg.deleted) {
    var delName = _tplSelectedName();
    if (delName === msg.deleted) {
      _tplEditorSelected = '';
      _tplEditorData = null;
    }
  }

  renderTemplatesPanel();

  // Load selected template detail (only if user already picked one)
  if (_tplEditorSelected && !_tplEditorNew) {
    var name = _tplSelectedName();
    var group = _currentGroup();
    send({ cmd: 'get_template', name: name, group: group, raw: true, scope: _tplEditorScope });
  }
}

function tplEditorReceiveDetail(msg) {
  if (msg.name !== _tplSelectedName()) return;
  _tplEditorData = msg.template || {};
  _tplEditorDirty = false;
  _tplEditorNew = false;
  renderTemplatesEditor();
}

// Selection is handled by tplEditorOnSelect (from dropdown) and
// tplEditorReceiveList (after save/delete)

/* ---- Render -------------------------------------------------------- */

function renderTemplatesPanel() {
  var panel = document.getElementById('panel-templates');
  if (!panel) return;

  var html = '';

  // Header bar
  html += '<div class="tpled-header">';
  html += '<span class="tpled-header-title">Templates</span>';
  html += '<select class="tpled-select" id="tpled-select" onchange="tplEditorOnSelect(this.value)">';
  html += '<option value="">Select\u2026</option>';
  var projectTpls = [];
  var userTpls = [];
  for (var i = 0; i < _tplEditorList.length; i++) {
    (_tplEditorList[i].global ? userTpls : projectTpls).push(_tplEditorList[i]);
  }
  if (projectTpls.length) {
    var projectDir = _tplShortenPath(projectTpls[0].dir || '');
    html += '<optgroup label="Project \u2014 ' + esc(projectDir) + '">';
    for (var i = 0; i < projectTpls.length; i++) {
      var key = 'project:' + projectTpls[i].name;
      var sel = key === _tplEditorSelected ? ' selected' : '';
      html += '<option value="' + esc(key) + '"' + sel + '>' + esc(projectTpls[i].name) + '</option>';
    }
    html += '</optgroup>';
  }
  if (userTpls.length) {
    var userDir = _tplShortenPath(userTpls[0].dir || '');
    html += '<optgroup label="User \u2014 ' + esc(userDir) + '">';
    for (var i = 0; i < userTpls.length; i++) {
      var key = 'user:' + userTpls[i].name;
      var sel = key === _tplEditorSelected ? ' selected' : '';
      var shadow = userTpls[i].shadowed ? ' (overridden)' : '';
      html += '<option value="' + esc(key) + '"' + sel + '>' + esc(userTpls[i].name) + shadow + '</option>';
    }
    html += '</optgroup>';
  }
  html += '</select>';
  html += '<button class="tpled-new-btn" onclick="tplEditorNew()" title="New template">+</button>';
  html += '<button class="tpled-new-btn" onclick="tplEditorLoad()" title="Refresh">&#x21BB;</button>';
  // View toggle
  html += '<div class="tpled-view-toggle">';
  html += '<button class="tpled-view-btn' + (_tplPanelView === 'editor' ? ' active' : '') + '" onclick="tplSwitchView(\'editor\')">Editor</button>';
  html += '<button class="tpled-view-btn' + (_tplPanelView === 'pipelines' ? ' active' : '') + '" onclick="tplSwitchView(\'pipelines\')">Pipelines</button>';
  html += '</div>';
  html += '</div>';

  // Content area depends on view
  if (_tplPanelView === 'pipelines') {
    html += '<div class="tpled-pipelines" id="tpled-pipelines"></div>';
  } else {
    html += '<div class="tpled-editor" id="tpled-editor"></div>';
  }

  panel.innerHTML = html;
  if (_tplPanelView === 'pipelines') {
    renderPipelinesView();
  } else {
    renderTemplatesEditor();
  }
}

function tplEditorOnSelect(key) {
  if (!key) {
    _tplEditorSelected = '';
    _tplEditorData = null;
    _tplEditorDirty = false;
    _tplEditorNew = false;
    renderTemplatesEditor();
    return;
  }
  _tplEditorSelected = key;
  var parts = key.split(':');
  var scope = parts[0];
  var name = parts.slice(1).join(':');
  _tplEditorScope = scope === 'user' ? 'user' : 'project';
  _tplEditorNew = false;
  _tplEditorData = null;
  _tplEditorDirty = false;
  renderTemplatesPanel();
  var group = _currentGroup();
  send({ cmd: 'get_template', name: name, group: group, raw: true, scope: _tplEditorScope });
}

function renderTemplatesEditor() {
  var el = document.getElementById('tpled-editor');
  if (!el) return;

  if (!_tplEditorData && !_tplEditorNew) {
    if (_tplEditorList.length === 0) {
      el.innerHTML = '<div class="tpled-empty">No templates found.<br>Click <b>+</b> to create one,<br>or add <code>.yaml</code> files to <code>.loom/templates/</code>.</div>';
    } else {
      el.innerHTML = '<div class="tpled-empty">'
        + '<b>' + _tplEditorList.length + '</b> template' + (_tplEditorList.length !== 1 ? 's' : '') + ' available.<br>'
        + 'Pick one from the dropdown above to view or edit.'
        + '</div>';
    }
    return;
  }

  var d = _tplEditorData || {};
  var agent = d.agent || {};

  var html = '<div class="tpled-form">';

  // Name + scope + description
  html += '<label>Name <span class="label-req">*</span></label>';
  html += '<input id="tpled-name" value="' + esc(d.name || _tplEditorSelected || '') + '" autocomplete="off" onchange="tplEditorMarkDirty()">';
  html += '<label>Scope</label>';
  html += '<select id="tpled-scope" onchange="tplEditorMarkDirty()">';
  html += '<option value="project"' + (_tplEditorScope === 'project' ? ' selected' : '') + '>Project (.loom/templates/)</option>';
  html += '<option value="user"' + (_tplEditorScope === 'user' ? ' selected' : '') + '>User (~/.loom/templates/)</option>';
  html += '</select>';
  html += '<label>Description</label>';
  html += '<input id="tpled-desc" value="' + esc(d.description || '') + '" autocomplete="off" onchange="tplEditorMarkDirty()">';

  // Agent block (collapsible)
  html += '<details class="tpled-section"' + (agent.name_prefix || agent.tab_color || agent.command ? ' open' : '') + '>';
  html += '<summary>Agent</summary>';
  html += '<label>Name prefix</label>';
  html += '<input id="tpled-agent-prefix" value="' + esc(agent.name_prefix || '') + '" placeholder="e.g. fix" autocomplete="off" onchange="tplEditorMarkDirty()">';
  html += '<label>Tab color</label>';
  html += '<input id="tpled-agent-color" value="' + esc(agent.tab_color || '') + '" placeholder="#hex" autocomplete="off" onchange="tplEditorMarkDirty()">';
  html += '<label>Boot command</label>';
  html += '<input id="tpled-agent-cmd" value="' + esc(agent.command || '') + '" placeholder="e.g. claude" autocomplete="off" onchange="tplEditorMarkDirty()">';
  html += '<label>Directory</label>';
  html += '<input id="tpled-agent-dir" value="' + esc(agent.directory || '') + '" autocomplete="off" onchange="tplEditorMarkDirty()">';
  html += '</details>';

  // Settings
  html += '<details class="tpled-section"' + (d.group || d.worktree ? ' open' : '') + '>';
  html += '<summary>Settings</summary>';
  html += '<label>Group</label>';
  html += '<input id="tpled-group" value="' + esc(d.group || '') + '" placeholder="Override target group" autocomplete="off" onchange="tplEditorMarkDirty()">';
  html += '<label class="gs-checkbox"><input id="tpled-worktree" type="checkbox"' + (d.worktree ? ' checked' : '') + ' onchange="tplEditorMarkDirty()"> Git worktree per agent</label>';
  html += '</details>';

  // Prompt field (coalesce old format on load)
  var prompt = d.prompt || '';
  if (!prompt) {
    var _parts = [];
    if (d.task) _parts.push(d.task);
    if (d.instructions) _parts.push(d.instructions);
    if (d.context) _parts.push(d.context);
    if (d.criteria) _parts.push(d.criteria);
    prompt = _parts.join('\n\n');
  }
  html += '<label>Prompt <span class="label-req">*</span> <span class="label-hint">must contain {{ TASK }}</span></label>';
  html += _tplHighlightWrap('tpled-prompt', prompt);
  html += '<label>Labels</label>';
  html += '<input id="tpled-labels" value="' + esc((d.labels || []).join(', ')) + '" placeholder="comma-separated" autocomplete="off" onchange="tplEditorMarkDirty()">';

  // Transitions (pipeline)
  var transitions = d.transitions || [];
  html += '<details class="tpled-section tpled-transitions-section"' + (transitions.length ? ' open' : '') + '>';
  html += '<summary>Transitions'
    + (transitions.length ? ' <span class="tpled-transitions-count">' + transitions.length + '</span>' : '')
    + ' <span class="hint-btn" onclick="event.preventDefault();event.stopPropagation();toggleHint(this)"'
    + ' data-hint="Defines which templates this one can derive into. Agents can only hand off to templates listed here. Leave empty for terminal pipeline stages.">?</span>'
    + '</summary>';
  html += '<div id="tpled-transitions">';
  for (var tri = 0; tri < transitions.length; tri++) {
    var tr = transitions[tri];
    html += _tplTransitionRow(tri, tr);
  }
  html += '</div>';
  html += '<button class="tpled-transition-add" onclick="tplAddTransition()">+ Add transition</button>';
  html += '</details>';

  // Variables (read-only, discovered)
  var vars = _tplEditorFindVars();
  if (vars.length > 0) {
    html += '<div class="tpled-vars-section">';
    html += '<label>Variables <span class="label-hint">auto-discovered</span></label>';
    html += '<div class="tpled-vars">';
    for (var vi = 0; vi < vars.length; vi++) {
      html += '<span class="tpled-var">' + esc(vars[vi]) + '</span>';
    }
    html += '</div></div>';
  }

  // Actions
  html += '<div class="tpled-actions">';
  html += '<button class="btn-primary" onclick="tplEditorSave()">Save</button>';
  html += '<button class="btn-cancel" onclick="tplEditorDuplicate()">Duplicate</button>';
  if (!_tplEditorNew) {
    html += '<button class="btn-cancel btn-danger" onclick="tplEditorDelete()">Delete</button>';
  }
  html += '</div>';

  html += '</div>';
  el.innerHTML = html;

  // Auto-resize all textareas to fit content
  el.querySelectorAll('textarea').forEach(_tplAutoResize);
}

function _tplAutoResize(el) {
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
  // Sync highlight backdrop
  var wrap = el.closest('.tpled-hl-wrap');
  if (wrap) {
    var bd = wrap.querySelector('.tpled-hl-backdrop');
    if (bd) {
      bd.innerHTML = _tplHighlightText(el.value);
      bd.style.height = el.style.height;
    }
  }
}

function _tplHighlightWrap(id, value) {
  var h = '<div class="tpled-hl-wrap">';
  h += '<div class="tpled-hl-backdrop" id="' + id + '-hl">' + _tplHighlightText(value) + '</div>';
  h += '<textarea id="' + id + '" rows="1"'
    + ' onchange="tplEditorMarkDirty()"'
    + ' oninput="_tplAutoResize(this)"'
    + ' onscroll="_tplSyncScroll(this)">'
    + esc(value) + '</textarea>';
  h += '</div>';
  return h;
}

function _tplShortenPath(p) {
  // Strip .loom/templates suffix to show the project/user root
  p = p.replace(/\/?\.loom\/templates\/?$/, '');
  // Replace home dir with ~
  if (typeof navigator !== 'undefined') {
    // Detect home from common prefixes
    var m = p.match(/^(\/Users\/[^/]+|\/home\/[^/]+)/);
    if (m) p = '~' + p.slice(m[1].length);
  }
  return p || '~';
}

function _tplHighlightText(text) {
  // Escape HTML first, then wrap Jinja2 expressions in spans
  var s = esc(text);
  // {{ expr | filter('default') }}
  s = s.replace(/(\{\{)(.*?)(\}\})/g, function(_, open, body, close) {
    var inner = body;
    // Strings inside the expression (single or double quotes)
    inner = inner.replace(/(&#39;[^]*?&#39;|&quot;[^]*?&quot;)/g,
      '<span class="tpled-hl-string">$1</span>');
    // Parentheses
    inner = inner.replace(/([()])/g, '<span class="tpled-hl-paren">$1</span>');
    // Filter pipes
    inner = inner.replace(/(\|\s*\w+)/g, '<span class="tpled-hl-filter">$1</span>');
    return '<span class="tpled-hl-expr">' + open + inner + close + '</span>';
  });
  // Trailing newline so backdrop height matches textarea
  if (!s.endsWith('\n')) s += '\n';
  return s;
}

function _tplSyncScroll(el) {
  var wrap = el.closest('.tpled-hl-wrap');
  if (wrap) {
    var bd = wrap.querySelector('.tpled-hl-backdrop');
    if (bd) {
      bd.scrollTop = el.scrollTop;
      bd.scrollLeft = el.scrollLeft;
    }
  }
}

function _tplEditorFindVars() {
  // Quick scan for {{ VAR }} in prompt field
  var texts = [];
  var el = document.getElementById('tpled-prompt');
  if (el) texts.push(el.value);
  // Also scan current data before form exists
  if (texts.join('').length === 0 && _tplEditorData) {
    var d = _tplEditorData;
    var prompt = d.prompt || '';
    if (!prompt) {
      var _parts = [];
      if (d.task) _parts.push(d.task);
      if (d.instructions) _parts.push(d.instructions);
      if (d.context) _parts.push(d.context);
      if (d.criteria) _parts.push(d.criteria);
      prompt = _parts.join('\n\n');
    }
    texts = [prompt];
  }
  var seen = {};
  var result = [];
  var re = /\{\{\s*(\w+)\s*/g;
  var combined = texts.join('\n');
  var m;
  while ((m = re.exec(combined)) !== null) {
    if (!seen[m[1]]) {
      seen[m[1]] = true;
      result.push(m[1]);
    }
  }
  return result;
}

/* ---- Actions ------------------------------------------------------- */

function _tplTransitionRow(idx, tr) {
  var isAsk = tr && tr.ask;
  var tpl = (tr && tr.template) || '';
  var when = (tr && tr.when) || '';
  var html = '<div class="tpled-transition-entry" data-idx="' + idx + '">';
  html += '<button class="tpled-tr-remove" onclick="tplRemoveTransition(' + idx + ')" title="Remove transition">\u2715</button>';
  html += '<div class="tpled-transition-body">';
  html += '<div class="tpled-transition-row">';
  html += '<div class="tpled-tr-field">';
  html += '<label class="tpled-tr-label">Type</label>';
  html += '<select class="tpled-tr-type" onchange="tplTransitionTypeChanged(this)">';
  html += '<option value="template"' + (!isAsk ? ' selected' : '') + '>Template</option>';
  html += '<option value="ask"' + (isAsk ? ' selected' : '') + '>Ask (human)</option>';
  html += '</select>';
  html += '</div>';
  // Template picker dropdown
  html += '<div class="tpled-tr-field">';
  html += '<label class="tpled-tr-label">Template</label>';
  html += '<select class="tpled-tr-template" onchange="tplEditorMarkDirty()"' + (isAsk ? ' disabled' : '') + '>';
  html += '<option value="">Select…</option>';
  var projectTpls = [];
  var userTpls = [];
  for (var i = 0; i < _tplEditorList.length; i++) {
    if (_tplEditorList[i].shadowed) continue;
    (_tplEditorList[i].global ? userTpls : projectTpls).push(_tplEditorList[i]);
  }
  if (projectTpls.length) {
    html += '<optgroup label="Project">';
    for (var i = 0; i < projectTpls.length; i++) {
      var sel = projectTpls[i].name === tpl ? ' selected' : '';
      html += '<option value="' + esc(projectTpls[i].name) + '"' + sel + '>' + esc(projectTpls[i].name) + '</option>';
    }
    html += '</optgroup>';
  }
  if (userTpls.length) {
    html += '<optgroup label="User">';
    for (var i = 0; i < userTpls.length; i++) {
      var sel = userTpls[i].name === tpl ? ' selected' : '';
      html += '<option value="' + esc(userTpls[i].name) + '"' + sel + '>' + esc(userTpls[i].name) + '</option>';
    }
    html += '</optgroup>';
  }
  // If the saved value doesn't match any known template, still show it
  if (tpl && !projectTpls.concat(userTpls).some(function(t) { return t.name === tpl; })) {
    html += '<option value="' + esc(tpl) + '" selected>' + esc(tpl) + ' (missing)</option>';
  }
  html += '</select>';
  html += '</div>';
  html += '</div>';
  html += '<label class="tpled-tr-when-label">When <span class="hint-btn" onclick="event.preventDefault();toggleHint(this)"'
    + ' data-hint="Describes when the agent should pick this transition. This text is included in the dispatch prompt so the agent knows which option to choose.">?</span></label>';
  html += '<textarea class="tpled-tr-when" placeholder="e.g. Implementation is complete and ready for review" rows="1" onchange="tplEditorMarkDirty()" oninput="this.style.height=\'auto\';this.style.height=this.scrollHeight+\'px\'">' + esc(when) + '</textarea>';
  html += '</div>';
  html += '</div>';
  return html;
}

function tplTransitionTypeChanged(selectEl) {
  var row = selectEl.closest('.tpled-transition-entry');
  if (!row) return;
  var tplSelect = row.querySelector('.tpled-tr-template');
  if (selectEl.value === 'ask') {
    tplSelect.disabled = true;
    tplSelect.value = '';
  } else {
    tplSelect.disabled = false;
  }
  tplEditorMarkDirty();
}

function tplAddTransition() {
  var container = document.getElementById('tpled-transitions');
  if (!container) return;
  var idx = container.querySelectorAll('.tpled-transition-entry').length;
  container.insertAdjacentHTML('beforeend', _tplTransitionRow(idx, {}));
  tplEditorMarkDirty();
}

function tplRemoveTransition(idx) {
  var container = document.getElementById('tpled-transitions');
  if (!container) return;
  var entries = container.querySelectorAll('.tpled-transition-entry');
  if (entries[idx]) entries[idx].remove();
  tplEditorMarkDirty();
}

function _tplReadTransitions() {
  var container = document.getElementById('tpled-transitions');
  if (!container) return [];
  var entries = container.querySelectorAll('.tpled-transition-entry');
  var result = [];
  for (var i = 0; i < entries.length; i++) {
    var type = entries[i].querySelector('.tpled-tr-type').value;
    var tpl = (entries[i].querySelector('.tpled-tr-template').value || '').trim();
    var when = (entries[i].querySelector('.tpled-tr-when').value || '').trim();
    if (type === 'ask') {
      result.push({ ask: true, when: when });
    } else if (tpl) {
      var entry = { template: tpl };
      if (when) entry.when = when;
      result.push(entry);
    }
  }
  return result;
}

function tplEditorMarkDirty() {
  _tplEditorDirty = true;
}

function tplEditorNew() {
  _tplEditorSelected = '';
  _tplEditorNew = true;
  _tplEditorDirty = true;
  _tplEditorScope = 'project';
  _tplEditorData = { name: '', description: '', agent: { name_prefix: '' } };
  renderTemplatesPanel();
  var inp = document.getElementById('tpled-name');
  if (inp) inp.focus();
}

function tplEditorSave() {
  var name = (document.getElementById('tpled-name').value || '').trim();
  if (!name) {
    document.getElementById('tpled-name').focus();
    return;
  }

  // Sanitize name for filename
  name = name.replace(/[^a-zA-Z0-9_-]/g, '-').toLowerCase();

  var labelsRaw = (document.getElementById('tpled-labels').value || '').trim();
  var labels = labelsRaw ? labelsRaw.split(',').map(function(s) { return s.trim(); }).filter(Boolean) : [];

  var prompt = document.getElementById('tpled-prompt').value || '';

  // Validate {{ TASK }} is present
  if (!/\{\{\s*TASK\s*(\|[^}]*)?\}\}/.test(prompt)) {
    var promptEl = document.getElementById('tpled-prompt');
    if (promptEl) { promptEl.focus(); promptEl.classList.add('input-error'); }
    _showToast('Prompt must contain {{ TASK }}', 'error');
    return;
  }

  var transitions = _tplReadTransitions();

  var tplData = {
    description: (document.getElementById('tpled-desc').value || '').trim(),
    agent: {
      name_prefix: (document.getElementById('tpled-agent-prefix').value || '').trim(),
      tab_color: (document.getElementById('tpled-agent-color').value || '').trim(),
      command: (document.getElementById('tpled-agent-cmd').value || '').trim(),
      directory: (document.getElementById('tpled-agent-dir').value || '').trim(),
    },
    group: (document.getElementById('tpled-group').value || '').trim(),
    worktree: document.getElementById('tpled-worktree').checked,
    prompt: prompt,
    labels: labels,
    transitions: transitions,
  };

  var scope = document.getElementById('tpled-scope').value || 'project';

  var msg = {
    cmd: 'save_template',
    name: name,
    template: tplData,
    group: _currentGroup(),
    scope: scope,
  };
  // If renaming or changing scope, include old name so the old file is removed
  var oldName = _tplSelectedName();
  var scopeChanged = !_tplEditorNew && scope !== _tplEditorScope;
  if (oldName && (oldName !== name || scopeChanged) && !_tplEditorNew) {
    msg.old_name = oldName;
  }

  _tplEditorSelected = scope + ':' + name;
  _tplEditorScope = scope;
  _tplEditorDirty = false;
  _tplEditorNew = false;
  send(msg);
}

function tplEditorDelete() {
  var name = _tplSelectedName();
  showConfirm('Delete template "' + name + '"?').then(function(yes) {
    if (!yes) return;
    send({ cmd: 'delete_template', name: name, group: _currentGroup() });
  });
}

function tplEditorDuplicate() {
  // Read current form values and start a "new" with them pre-filled
  var name = (document.getElementById('tpled-name').value || '').trim();
  _tplEditorNew = true;
  _tplEditorDirty = true;
  _tplEditorSelected = '';
  // Keep the form data but clear name
  _tplEditorData = _tplEditorReadForm();
  _tplEditorData.name = name ? name + '-copy' : '';
  renderTemplatesPanel();
  var inp = document.getElementById('tpled-name');
  if (inp) { inp.focus(); inp.select(); }
}

function _tplEditorReadForm() {
  var labelsRaw = (document.getElementById('tpled-labels').value || '').trim();
  return {
    name: (document.getElementById('tpled-name').value || '').trim(),
    description: (document.getElementById('tpled-desc').value || '').trim(),
    agent: {
      name_prefix: (document.getElementById('tpled-agent-prefix').value || '').trim(),
      tab_color: (document.getElementById('tpled-agent-color').value || '').trim(),
      command: (document.getElementById('tpled-agent-cmd').value || '').trim(),
      directory: (document.getElementById('tpled-agent-dir').value || '').trim(),
    },
    group: (document.getElementById('tpled-group').value || '').trim(),
    worktree: document.getElementById('tpled-worktree').checked,
    prompt: document.getElementById('tpled-prompt').value || '',
    labels: labelsRaw ? labelsRaw.split(',').map(function(s) { return s.trim(); }).filter(Boolean) : [],
    transitions: _tplReadTransitions(),
  };
}

/* ---- Pipeline view -------------------------------------------------- */

function tplSwitchView(view) {
  _tplPanelView = view;
  if (view === 'pipelines') _tplPipelinesData = null;
  renderTemplatesPanel();
}

function renderPipelinesView() {
  var el = document.getElementById('tpled-pipelines');
  if (!el) return;

  if (!_tplPipelinesData) {
    el.innerHTML = '<div class="tpled-pipelines-loading">Loading…</div>';
    send({ cmd: 'discover_pipelines', group: _currentGroup() || '' });
    return;
  }

  var pipelines = _tplPipelinesData.pipelines || [];
  if (!pipelines.length) {
    el.innerHTML = '<div class="tpled-pipelines-empty">'
      + 'No pipelines found.<br>'
      + '<span class="dim">Add <code>transitions</code> to your templates.</span></div>';
    return;
  }

  var html = '';
  for (var pi = 0; pi < pipelines.length; pi++) {
    var p = pipelines[pi];
    html += '<div class="pipeline-graph">';
    html += '<div class="pipeline-graph-title">' + esc(p.name)
      + ' <span class="dim">(' + p.templates.length + ' templates)</span></div>';

    // Build adjacency for layout
    var adj = {};
    var incoming = {};
    for (var ei = 0; ei < p.edges.length; ei++) {
      var e = p.edges[ei];
      adj[e.from] = adj[e.from] || [];
      adj[e.from].push(e);
      incoming[e.to] = (incoming[e.to] || 0) + 1;
    }

    // BFS to assign depth levels
    var depths = {};
    var entryPoints = [];
    for (var ti = 0; ti < p.templates.length; ti++) {
      if (!incoming[p.templates[ti]]) entryPoints.push(p.templates[ti]);
    }
    if (!entryPoints.length) entryPoints = [p.templates[0]];

    var queue = [];
    for (var epi = 0; epi < entryPoints.length; epi++) {
      depths[entryPoints[epi]] = 0;
      queue.push(entryPoints[epi]);
    }
    while (queue.length) {
      var node = queue.shift();
      var edges = adj[node] || [];
      for (var eii = 0; eii < edges.length; eii++) {
        var target = edges[eii].to;
        if (depths[target] === undefined) {
          depths[target] = (depths[node] || 0) + 1;
          queue.push(target);
        }
      }
    }
    // Assign remaining (cyclic-only nodes)
    for (var ti = 0; ti < p.templates.length; ti++) {
      if (depths[p.templates[ti]] === undefined) depths[p.templates[ti]] = 0;
    }

    // Group by depth
    var maxDepth = 0;
    var byDepth = {};
    for (var tname in depths) {
      var d = depths[tname];
      if (d > maxDepth) maxDepth = d;
      byDepth[d] = byDepth[d] || [];
      byDepth[d].push(tname);
    }

    // Render nodes by depth
    html += '<div class="pipeline-graph-nodes">';
    for (var d = 0; d <= maxDepth; d++) {
      var nodes = byDepth[d] || [];
      for (var ni = 0; ni < nodes.length; ni++) {
        var n = nodes[ni];
        var nodeEdges = adj[n] || [];
        var isTerminal = !nodeEdges.length;
        var cls = isTerminal ? ' pipeline-node-terminal' : '';
        html += '<div class="pipeline-node' + cls + '"'
          + ' onclick="tplSwitchView(\'editor\');tplEditorOnSelect(\'project:' + esc(n) + '\')"'
          + ' title="Click to edit">';
        html += '<div class="pipeline-node-name">' + esc(n) + '</div>';
        if (nodeEdges.length) {
          html += '<div class="pipeline-node-edges">';
          for (var nei = 0; nei < nodeEdges.length; nei++) {
            var ne = nodeEdges[nei];
            html += '<div class="pipeline-node-edge">→ ' + esc(ne.to);
            if (ne.when) html += ' <span class="dim">"' + esc(ne.when) + '"</span>';
            html += '</div>';
          }
          html += '</div>';
        } else {
          html += '<div class="pipeline-node-edges"><div class="pipeline-node-edge dim">(terminal)</div></div>';
        }
        html += '</div>';
      }
    }
    html += '</div></div>';
  }

  el.innerHTML = html;
}

function tplReceivePipelines(msg) {
  _tplPipelinesData = msg;
  if (_tplPanelView === 'pipelines') renderPipelinesView();
}

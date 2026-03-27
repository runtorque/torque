/* ------------------------------------------------------------------ */
/* Templates panel app — template editor in the taskbar panel          */
/* ------------------------------------------------------------------ */

var _tplEditorList = [];       // cached template list
var _tplEditorSelected = '';   // selected template name
var _tplEditorData = null;     // loaded template data (parsed dict)
var _tplEditorDirty = false;
var _tplEditorNew = false;     // true when creating a new template
var _tplEditorScope = 'project'; // 'project' or 'user'

/* ---- Load & render ------------------------------------------------- */

function tplEditorLoad() {
  var group = _currentGroup();
  send({ cmd: 'list_templates', group: group });
}

function tplEditorReceiveList(msg) {
  _tplEditorList = msg.templates || [];

  // If we just saved or the selected template still exists, keep selection
  if (msg.saved) _tplEditorSelected = msg.saved;
  if (msg.deleted && _tplEditorSelected === msg.deleted) {
    _tplEditorSelected = '';
    _tplEditorData = null;
  }

  renderTemplatesPanel();

  // Load selected template detail (only if user already picked one)
  if (_tplEditorSelected && !_tplEditorNew) {
    tplEditorSelectTemplate(_tplEditorSelected);
  }
}

function tplEditorReceiveDetail(msg) {
  if (msg.name !== _tplEditorSelected) return;
  _tplEditorData = msg.template || {};
  _tplEditorDirty = false;
  _tplEditorNew = false;
  renderTemplatesEditor();
}

function tplEditorSelectTemplate(name) {
  if (_tplEditorDirty) {
    // Discard changes silently — form is small, low stakes
  }
  _tplEditorSelected = name;
  _tplEditorNew = false;
  _tplEditorData = null;
  _tplEditorDirty = false;
  // Determine scope from list metadata
  for (var i = 0; i < _tplEditorList.length; i++) {
    if (_tplEditorList[i].name === name) {
      _tplEditorScope = _tplEditorList[i].global ? 'user' : 'project';
      break;
    }
  }
  renderTemplatesPanel();
  var group = _currentGroup();
  send({ cmd: 'get_template', name: name, group: group, raw: true });
}

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
    html += '<optgroup label="Project">';
    for (var i = 0; i < projectTpls.length; i++) {
      var sel = projectTpls[i].name === _tplEditorSelected ? ' selected' : '';
      html += '<option value="' + esc(projectTpls[i].name) + '"' + sel + '>' + esc(projectTpls[i].name) + '</option>';
    }
    html += '</optgroup>';
  }
  if (userTpls.length) {
    html += '<optgroup label="User">';
    for (var i = 0; i < userTpls.length; i++) {
      var sel = userTpls[i].name === _tplEditorSelected ? ' selected' : '';
      html += '<option value="' + esc(userTpls[i].name) + '"' + sel + '>' + esc(userTpls[i].name) + '</option>';
    }
    html += '</optgroup>';
  }
  html += '</select>';
  html += '<button class="tpled-new-btn" onclick="tplEditorNew()" title="New template">+</button>';
  html += '<button class="tpled-new-btn" onclick="tplEditorLoad()" title="Refresh">&#x21BB;</button>';
  html += '</div>';

  // Editor area
  html += '<div class="tpled-editor" id="tpled-editor"></div>';

  panel.innerHTML = html;
  renderTemplatesEditor();
}

function tplEditorOnSelect(name) {
  if (!name) {
    _tplEditorSelected = '';
    _tplEditorData = null;
    _tplEditorDirty = false;
    _tplEditorNew = false;
    renderTemplatesEditor();
    return;
  }
  tplEditorSelectTemplate(name);
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

  // Task fields (with Jinja2 highlighting)
  html += '<label>Task</label>';
  html += _tplHighlightWrap('tpled-task', d.task || d.prompt || '');
  html += '<label>Instructions</label>';
  html += _tplHighlightWrap('tpled-instructions', d.instructions || '');
  html += '<label>Context</label>';
  html += _tplHighlightWrap('tpled-context', d.context || '');
  html += '<label>Criteria</label>';
  html += _tplHighlightWrap('tpled-criteria', d.criteria || '');
  html += '<label>Labels</label>';
  html += '<input id="tpled-labels" value="' + esc((d.labels || []).join(', ')) + '" placeholder="comma-separated" autocomplete="off" onchange="tplEditorMarkDirty()">';

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
  // Quick scan for {{ VAR }} and {{ VAR | default(...) }} in all text fields
  var texts = [];
  var el;
  var ids = ['tpled-task', 'tpled-instructions', 'tpled-context', 'tpled-criteria'];
  for (var i = 0; i < ids.length; i++) {
    el = document.getElementById(ids[i]);
    if (el) texts.push(el.value);
  }
  // Also scan current data before form exists
  if (texts.join('').length === 0 && _tplEditorData) {
    var d = _tplEditorData;
    texts = [d.task || d.prompt || '', d.instructions || '', d.context || '', d.criteria || ''];
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
    task: document.getElementById('tpled-task').value || '',
    instructions: document.getElementById('tpled-instructions').value || '',
    context: document.getElementById('tpled-context').value || '',
    criteria: document.getElementById('tpled-criteria').value || '',
    labels: labels,
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
  var scopeChanged = !_tplEditorNew && scope !== _tplEditorScope;
  if (_tplEditorSelected && (_tplEditorSelected !== name || scopeChanged) && !_tplEditorNew) {
    msg.old_name = _tplEditorSelected;
  }

  _tplEditorSelected = name;
  _tplEditorScope = scope;
  _tplEditorDirty = false;
  _tplEditorNew = false;
  send(msg);
}

function tplEditorDelete() {
  showConfirm('Delete template "' + _tplEditorSelected + '"?').then(function(yes) {
    if (!yes) return;
    send({ cmd: 'delete_template', name: _tplEditorSelected, group: _currentGroup() });
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
    task: document.getElementById('tpled-task').value || '',
    instructions: document.getElementById('tpled-instructions').value || '',
    context: document.getElementById('tpled-context').value || '',
    criteria: document.getElementById('tpled-criteria').value || '',
    labels: labelsRaw ? labelsRaw.split(',').map(function(s) { return s.trim(); }).filter(Boolean) : [],
  };
}

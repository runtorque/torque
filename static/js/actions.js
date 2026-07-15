/* ------------------------------------------------------------------ */
/* Actions panel app — action editor in the taskbar panel              */
/* ------------------------------------------------------------------ */

var _tplEditorList = [];       // cached template list
var _tplEditorSelected = '';   // selected template name
var _tplEditorData = null;     // loaded template data (parsed dict)
var _tplEditorDirty = false;
var _tplEditorNew = false;     // true when creating a new template
var _tplEditorScope = 'project'; // 'project' or 'user'
var _tplPanelView = 'editor';  // 'editor' or 'pipelines'
var _tplPipelinesData = null;  // cached pipeline discovery result
var _tplEditorLoadedGroup = null;
var _tplEditorLoadingGroup = null;

/* ---- Load & render ------------------------------------------------- */

function tplEditorLoad() {
  var group = _currentGroup();
  _tplEditorLoadingGroup = group || '';
  send({ cmd: 'list_actions', group: group });
}

function tplEditorEnsureLoaded() {
  var group = _currentGroup() || '';
  if (_tplEditorLoadedGroup === group || _tplEditorLoadingGroup === group) return;
  tplEditorLoad();
}

function tplEditorBeginGroupSwitch() {
  var group = _currentGroup() || '';
  if (_tplEditorLoadedGroup !== group) {
    _tplEditorList = [];
    _tplEditorSelected = '';
    _tplEditorData = null;
    _tplEditorDirty = false;
    _tplEditorNew = false;
    _tplEditorLoadedGroup = null;
  }
  tplEditorLoad();
  renderTemplatesPanel();
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
  var msgGroup = (msg && msg.group != null) ? (msg.group || '') : (_currentGroup() || '');
  var currentGroup = _currentGroup() || '';
  if (msgGroup !== currentGroup) {
    if (_tplEditorLoadingGroup === msgGroup) _tplEditorLoadingGroup = null;
    return;
  }
  _tplEditorLoadingGroup = null;
  _tplEditorLoadedGroup = msgGroup;
  _tplEditorList = msg.actions || [];

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
    send({ cmd: 'get_action', name: name, group: group, raw: true, scope: _tplEditorScope });
  }
}

function tplEditorReceiveDetail(msg) {
  if (msg.name !== _tplSelectedName()) return;
  _tplEditorData = msg.action || {};
  _tplEditorDirty = false;
  _tplEditorNew = false;
  renderTemplatesEditor();
}

// Selection is handled by tplEditorOnSelect (from dropdown) and
// tplEditorReceiveList (after save/delete)

/* ---- Render -------------------------------------------------------- */

function renderTemplatesPanel() {
  var panel = document.getElementById('panel-actions');
  if (!panel) return;
  var scopeGroup = (typeof _currentGroup === 'function' ? _currentGroup() : '') || '';
  var loadedForScope = _tplEditorLoadedGroup === scopeGroup
    || (_tplEditorLoadedGroup == null && _tplEditorLoadingGroup !== scopeGroup);
  var listForScope = loadedForScope ? _tplEditorList : [];
  var selectedForScope = loadedForScope ? _tplEditorSelected : '';

  var html = '';

  // Header bar
  html += '<div class="tpled-header ui-panel-header ui-panel-header--surface">';
  html += '<div class="tpled-header-copy ui-panel-header__copy">';
  html += '<div class="tpled-header-title-row ui-panel-header__title-row">';
  html += '<span class="tpled-header-title ui-panel-header__title">Actions</span>';
  html += '</div>';
  html += '<div class="tpled-header-subtitle ui-panel-header__subtitle">Prompt templates, pipelines, and dispatch workflows.</div>';
  html += '</div>';
  html += '</div>';
  html += '<div class="tpled-header-controls ui-toolbar ui-toolbar--bordered">';
  html += '<select class="tpled-select" id="tpled-select" onchange="tplEditorOnSelect(this.value)">';
  html += '<option value="">Select\u2026</option>';
  var projectTpls = [];
  var userTpls = [];
  for (var i = 0; i < listForScope.length; i++) {
    (listForScope[i].global ? userTpls : projectTpls).push(listForScope[i]);
  }
  if (projectTpls.length) {
    var projectDir = _tplShortenPath(projectTpls[0].dir || '');
    html += '<optgroup label="Project \u2014 ' + esc(projectDir) + '">';
    for (var i = 0; i < projectTpls.length; i++) {
      var key = 'project:' + projectTpls[i].name;
      var sel = key === selectedForScope ? ' selected' : '';
      html += '<option value="' + esc(key) + '"' + sel + '>' + esc(projectTpls[i].name) + '</option>';
    }
    html += '</optgroup>';
  }
  if (userTpls.length) {
    var userDir = _tplShortenPath(userTpls[0].dir || '');
    html += '<optgroup label="User \u2014 ' + esc(userDir) + '">';
    for (var i = 0; i < userTpls.length; i++) {
      var key = 'user:' + userTpls[i].name;
      var sel = key === selectedForScope ? ' selected' : '';
      var shadow = userTpls[i].shadowed ? ' (overridden)' : '';
      html += '<option value="' + esc(key) + '"' + sel + '>' + esc(userTpls[i].name) + shadow + '</option>';
    }
    html += '</optgroup>';
  }
  html += '</select>';
  html += '<button class="tpled-new-btn" onclick="tplEditorNew()" title="New action">+</button>';
  html += '<button class="tpled-new-btn" onclick="tplEditorLoad()" title="Refresh">&#x21BB;</button>';
  // View toggle
  html += '<div class="segmented-control tpled-view-toggle" role="group" aria-label="Action view">';
  html += '<button type="button" class="segmented-control__item tpled-view-btn' + (_tplPanelView === 'editor' ? ' active' : '') + '" aria-pressed="' + (_tplPanelView === 'editor' ? 'true' : 'false') + '" onclick="tplSwitchView(\'editor\')">Editor</button>';
  html += '<button type="button" class="segmented-control__item tpled-view-btn' + (_tplPanelView === 'pipelines' ? ' active' : '') + '" aria-pressed="' + (_tplPanelView === 'pipelines' ? 'true' : 'false') + '" onclick="tplSwitchView(\'pipelines\')">DAG</button>';
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
  send({ cmd: 'get_action', name: name, group: group, raw: true, scope: _tplEditorScope });
}

function renderTemplatesEditor() {
  var el = document.getElementById('tpled-editor');
  if (!el) return;
  var restoreState = _tplCaptureEditorUiState(el);
  var scopeGroup = (typeof _currentGroup === 'function' ? _currentGroup() : '') || '';
  var loadedForScope = _tplEditorLoadedGroup === scopeGroup
    || (_tplEditorLoadedGroup == null && _tplEditorLoadingGroup !== scopeGroup);
  var listForScope = loadedForScope ? _tplEditorList : [];
  var dataForScope = loadedForScope ? _tplEditorData : null;
  var newForScope = loadedForScope ? _tplEditorNew : false;

  if (!loadedForScope) {
    el.innerHTML = '<div class="tpled-empty">Loading actions\u2026</div>';
    return;
  }

  if (!dataForScope && !newForScope) {
    if (listForScope.length === 0) {
      el.innerHTML = '<div class="tpled-empty">No actions found.<br>Click <b>+</b> to create one,<br>or add <code>.yaml</code> files to <code>.torque/actions/</code>.</div>';
    } else {
      el.innerHTML = '<div class="tpled-empty">'
        + '<b>' + listForScope.length + '</b> action' + (listForScope.length !== 1 ? 's' : '') + ' available.<br>'
        + 'Pick one from the dropdown above to view or edit.'
        + '</div>';
    }
    return;
  }

  var d = dataForScope || {};
  var agentTemplate = typeof d.agent === 'string' ? d.agent : '';
  var agent = typeof d.agent === 'string' ? {} : (d.agent || {});
  var agentUsesTemplate = !!agentTemplate;

  var html = '<div class="tpled-form form-control-group-sm">';

  // Name + scope + description
  html += '<label>Name <span class="label-req">*</span></label>';
  html += '<input id="tpled-name" value="' + esc(d.name || _tplEditorSelected || '') + '" autocomplete="off" onchange="tplEditorMarkDirty()">';
  html += '<label>Scope</label>';
  html += '<select id="tpled-scope" onchange="tplEditorMarkDirty()">';
  html += '<option value="project"' + (_tplEditorScope === 'project' ? ' selected' : '') + '>Project (.torque/actions/)</option>';
  html += '<option value="user"' + (_tplEditorScope === 'user' ? ' selected' : '') + '>User (~/.torque/actions/)</option>';
  html += '</select>';
  html += '<label>Description</label>';
  html += '<input id="tpled-desc" value="' + esc(d.description || '') + '" autocomplete="off" onchange="tplEditorMarkDirty()">';

  // Agent block (collapsible)
  html += '<details class="tpled-section"' + (agentUsesTemplate || agent.name_prefix || agent.tab_color || agent.command ? ' open' : '') + '>';
  html += '<summary>Agent</summary>';
  html += '<label>Role</label>';
  html += '<select id="tpled-agent-template" onchange="tplEditorToggleAgentMode();tplEditorMarkDirty()">';
  html += '<option value="">Legacy inline config</option>';
  var projectAgentTpls = (_cachedAgentTemplates || []).filter(function(t) { return !t.global && !t.shadowed; });
  var userAgentTpls = (_cachedAgentTemplates || []).filter(function(t) { return t.global && !t.shadowed; });
  if (projectAgentTpls.length) {
    html += '<optgroup label="Project">';
    for (var ati = 0; ati < projectAgentTpls.length; ati++) {
      var aSel = projectAgentTpls[ati].name === agentTemplate ? ' selected' : '';
      html += '<option value="' + esc(projectAgentTpls[ati].name) + '"' + aSel + '>' + esc(projectAgentTpls[ati].display_name || projectAgentTpls[ati].name) + '</option>';
    }
    html += '</optgroup>';
  }
  if (userAgentTpls.length) {
    html += '<optgroup label="User">';
    for (var aui = 0; aui < userAgentTpls.length; aui++) {
      var auSel = userAgentTpls[aui].name === agentTemplate ? ' selected' : '';
      html += '<option value="' + esc(userAgentTpls[aui].name) + '"' + auSel + '>' + esc(userAgentTpls[aui].display_name || userAgentTpls[aui].name) + '</option>';
    }
    html += '</optgroup>';
  }
  html += '</select>';
  html += '<div class="tpled-inline-agent-fields' + (agentUsesTemplate ? ' hidden' : '') + '" id="tpled-inline-agent-fields">';
  html += '<label>Name prefix</label>';
  html += '<input id="tpled-agent-prefix" value="' + esc(agent.name_prefix || '') + '" placeholder="e.g. fix" autocomplete="off" onchange="tplEditorMarkDirty()">';
  html += '<label>Tab color</label>';
  html += '<input id="tpled-agent-color" value="' + esc(agent.tab_color || '') + '" placeholder="#hex" autocomplete="off" onchange="tplEditorMarkDirty()">';
  html += '<label>Boot command</label>';
  html += '<input id="tpled-agent-cmd" value="' + esc(agent.command || '') + '" placeholder="e.g. claude" autocomplete="off" onchange="tplEditorMarkDirty()">';
  html += '<label>Directory</label>';
  html += '<input id="tpled-agent-dir" value="' + esc(agent.directory || '') + '" autocomplete="off" onchange="tplEditorMarkDirty()">';
  html += '</div>';
  html += '</details>';

  // Settings
  html += '<details class="tpled-section"' + (d.group || d.worktree || d.auto_close_on_done || d.implementation_depth ? ' open' : '') + '>';
  html += '<summary>Settings</summary>';
  html += '<label>Group</label>';
  html += '<input id="tpled-group" value="' + esc(d.group || '') + '" placeholder="Override target group" autocomplete="off" onchange="tplEditorMarkDirty()">';
  html += '<label class="gs-checkbox"><input id="tpled-worktree" type="checkbox"' + (d.worktree ? ' checked' : '') + ' onchange="tplEditorMarkDirty()"> Git worktree per agent</label>';
  html += '<label class="gs-checkbox"><input id="tpled-auto-close-on-done" type="checkbox"' + (d.auto_close_on_done ? ' checked' : '') + ' onchange="tplEditorMarkDirty()"> Auto-close agent after root task is done</label>';
  html += '<label class="gs-checkbox"><input id="tpled-implementation-depth" type="checkbox"' + (d.implementation_depth ? ' checked' : '') + ' onchange="tplEditorMarkDirty()"> Implementation-depth action (code-mutating; review gate eligible)</label>';
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
  html += '<label class="gs-checkbox"><input id="tpled-disable-role-preamble" type="checkbox"' + (d.disable_role_preamble ? ' checked' : '') + ' onchange="tplEditorMarkDirty()"> Disable role preamble (don\'t inject the worker\'s role preamble for this action)</label>';
  html += '<label>Labels</label>';
  html += '<input id="tpled-labels" value="' + esc((d.labels || []).join(', ')) + '" placeholder="comma-separated" autocomplete="off" onchange="tplEditorMarkDirty()">';

  // Transitions (pipeline)
  var transitions = d.transitions || [];
  html += '<details class="tpled-section tpled-transitions-section"' + (transitions.length ? ' open' : '') + '>';
  html += '<summary>Transitions'
    + (transitions.length ? ' <span class="tpled-transitions-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">' + transitions.length + '</span>' : '')
    + ' <span class="hint-btn" onclick="event.preventDefault();event.stopPropagation();toggleHint(this)"'
    + ' data-hint="Defines which actions this one can derive into. Agents can only hand off to actions listed here. Leave empty for terminal pipeline stages.">?</span>'
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

  _tplRestoreEditorUiState(el, restoreState);

  // Auto-resize all textareas to fit content
  el.querySelectorAll('textarea').forEach(_tplAutoResize);
}

function _tplCaptureEditorUiState(root) {
  var snapshot = { focus: null, locGates: {} };
  if (!root) return snapshot;
  var active = document.activeElement;
  if (active && typeof root.contains === 'function' && root.contains(active)) {
    var focusKey = active.id || (active.dataset ? active.dataset.focusKey : '');
    if (focusKey) {
      snapshot.focus = {
        key: focusKey,
        byId: !!active.id,
        value: ('value' in active) ? active.value : null,
        checked: ('checked' in active) ? !!active.checked : null,
        selectionStart: typeof active.selectionStart === 'number' ? active.selectionStart : null,
        selectionEnd: typeof active.selectionEnd === 'number' ? active.selectionEnd : null,
      };
    }
  }
  if (typeof root.querySelectorAll === 'function') {
    root.querySelectorAll('.tpled-transition-entry').forEach(function(row) {
      var idx = row.getAttribute ? row.getAttribute('data-idx') : (row.dataset ? row.dataset.idx : '');
      if (idx == null || idx === '') return;
      var details = row.querySelector('.tpled-loc-gate-details');
      var enabled = row.querySelector('.tpled-loc-gate-enabled');
      var ship = row.querySelector('.tpled-loc-ship-direct');
      var review = row.querySelector('.tpled-loc-review-above');
      var bypass = row.querySelector('.tpled-loc-bypass');
      snapshot.locGates[idx] = {
        open: details ? !!details.open : false,
        enabled: enabled ? !!enabled.checked : false,
        ship_direct_max: ship ? ship.value : '',
        review_default_above: review ? review.value : '',
        self_review_bypass_allowed: bypass ? !!bypass.checked : false,
      };
    });
  }
  return snapshot;
}

function _tplRestoreEditorUiState(root, snapshot) {
  if (!root || !snapshot) return;
  var locGates = snapshot.locGates || {};
  Object.keys(locGates).forEach(function(idx) {
    var row = root.querySelector
      ? root.querySelector('.tpled-transition-entry[data-idx="' + idx + '"]')
      : null;
    if (!row) return;
    var saved = locGates[idx] || {};
    var details = row.querySelector('.tpled-loc-gate-details');
    var enabled = row.querySelector('.tpled-loc-gate-enabled');
    var ship = row.querySelector('.tpled-loc-ship-direct');
    var review = row.querySelector('.tpled-loc-review-above');
    var bypass = row.querySelector('.tpled-loc-bypass');
    if (details) details.open = !!saved.open;
    if (enabled) enabled.checked = !!saved.enabled;
    if (ship) ship.value = saved.ship_direct_max || '';
    if (review) review.value = saved.review_default_above || '';
    if (bypass) bypass.checked = !!saved.self_review_bypass_allowed;
    _tplSyncLocGateRow(row);
  });
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

function tplEditorToggleAgentMode() {
  var sel = document.getElementById('tpled-agent-template');
  var wrap = document.getElementById('tpled-inline-agent-fields');
  if (!sel || !wrap) return;
  wrap.classList.toggle('hidden', !!sel.value);
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
  // Strip .torque/actions suffix to show the project/user root
  p = p.replace(/\/?\.torque\/actions\/?$/, '');
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
  var tpl = (tr && tr.action) || '';
  var when = (tr && tr.when) || '';
  var target = (tr && tr.target) || '';
  var status = (tr && tr.status) || '';
  var locGate = (tr && tr.loc_gate && typeof tr.loc_gate === 'object') ? tr.loc_gate : null;
  var locEnabled = !!locGate;
  var locShip = locGate && locGate.ship_direct_max != null ? String(locGate.ship_direct_max) : '';
  var locReview = locGate && locGate.review_default_above != null ? String(locGate.review_default_above) : '';
  var locBypass = !!(locGate && locGate.self_review_bypass_allowed);
  var locDisabled = locEnabled ? '' : ' disabled';
  var html = '<div class="tpled-transition-entry" data-idx="' + idx + '">';
  html += '<button class="tpled-tr-remove" onclick="tplRemoveTransition(' + idx + ')" title="Delete transition">\u2715</button>';
  html += '<div class="tpled-transition-body">';
  html += '<div class="tpled-transition-row">';
  html += '<div class="tpled-tr-field">';
  html += '<label class="tpled-tr-label">Type</label>';
  html += '<select class="tpled-tr-type" onchange="tplTransitionTypeChanged(this)">';
  html += '<option value="action"' + (!isAsk ? ' selected' : '') + '>Action</option>';
  html += '<option value="ask"' + (isAsk ? ' selected' : '') + '>Ask (human)</option>';
  html += '</select>';
  html += '</div>';
  // Template picker dropdown
  var hideIfAsk = isAsk ? ' style="display:none"' : '';
  html += '<div class="tpled-tr-field tpled-tr-action-field"' + hideIfAsk + '>';
  html += '<label class="tpled-tr-label">Action</label>';
  html += '<select class="tpled-tr-template" onchange="tplEditorMarkDirty()">';
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
  // Target (agent handoff)
  html += '<div class="tpled-tr-field tpled-tr-target-field"' + hideIfAsk + '>';
  html += '<label class="tpled-tr-label">Target</label>';
  html += '<select class="tpled-tr-target" onchange="tplEditorMarkDirty()">';
  html += '<option value=""' + (!target ? ' selected' : '') + '>New agent</option>';
  html += '<option value="self"' + (target === 'self' ? ' selected' : '') + '>Self</option>';
  html += '<option value="parent"' + (target === 'parent' ? ' selected' : '') + '>Parent agent</option>';
  html += '<option value="root"' + (target === 'root' ? ' selected' : '') + '>Root agent</option>';
  html += '</select>';
  html += '</div>';
  html += '</div>';
  // Status badge
  html += '<div class="tpled-transition-row tpled-tr-status-row"' + hideIfAsk + '>';
  html += '<div class="tpled-tr-field" style="flex:1">';
  html += '<label class="tpled-tr-label">Status</label>';
  html += '<input class="tpled-tr-status" type="text" placeholder="e.g. On Review" value="' + esc(status) + '" onchange="tplEditorMarkDirty()">';
  html += '</div>';
  html += '</div>';
  html += '<label class="tpled-tr-when-label">When <span class="hint-btn" onclick="event.preventDefault();toggleHint(this)"'
    + ' data-hint="Describes when the agent should pick this transition. This text is included in the dispatch prompt so the agent knows which option to choose.">?</span></label>';
  html += '<textarea class="tpled-tr-when" placeholder="e.g. Implementation is complete and ready for review" rows="1" onchange="tplEditorMarkDirty()" oninput="this.style.height=\'auto\';this.style.height=this.scrollHeight+\'px\'">' + esc(when) + '</textarea>';
  html += '<details class="tpled-loc-gate-details tpled-tr-action-field"' + (locEnabled ? ' open' : '') + hideIfAsk + ' ontoggle="tplLocGateToggled(this)">';
  html += '<summary>Mandatory transition threshold (LOC) <span class="hint-btn" onclick="event.preventDefault();event.stopPropagation();toggleHint(this)"'
    + ' data-hint="Optional transition-local threshold. When enabled, this transition must be triggered above the configured LOC threshold.">?</span></summary>';
  html += '<label class="gs-checkbox tpled-loc-enable-label"><input id="tpled-tr-' + idx + '-loc-enabled" data-focus-key="transition:' + idx + ':loc-enabled" class="tpled-loc-gate-enabled" type="checkbox"' + (locEnabled ? ' checked' : '') + ' onchange="tplLocGateEnabledChanged(this)"> Require this transition above N LOC</label>';
  html += '<div class="tpled-loc-gate-fields">';
  html += '<div class="tpled-transition-row">';
  html += '<div class="tpled-tr-field">';
  html += '<label class="tpled-tr-label">Ship direct ≤</label>';
  html += '<input id="tpled-tr-' + idx + '-loc-ship-direct" data-focus-key="transition:' + idx + ':loc-ship-direct" class="tpled-loc-ship-direct" type="number" min="0" value="' + esc(locShip) + '" placeholder="50" onchange="tplEditorMarkDirty()"' + locDisabled + '>';
  html += '</div>';
  html += '<div class="tpled-tr-field">';
  html += '<label class="tpled-tr-label">Review above</label>';
  html += '<input id="tpled-tr-' + idx + '-loc-review-above" data-focus-key="transition:' + idx + ':loc-review-above" class="tpled-loc-review-above" type="number" min="0" value="' + esc(locReview) + '" placeholder="150" onchange="tplEditorMarkDirty()"' + locDisabled + '>';
  html += '</div>';
  html += '</div>';
  html += '<label class="gs-checkbox tpled-loc-bypass-label"><input id="tpled-tr-' + idx + '-loc-bypass" data-focus-key="transition:' + idx + ':loc-bypass" class="tpled-loc-bypass" type="checkbox"' + (locBypass ? ' checked' : '') + locDisabled + ' onchange="tplEditorMarkDirty()"> allow self-review bypass</label>';
  html += '</div>';
  html += '</details>';
  html += '</div>';
  html += '</div>';
  return html;
}

function tplTransitionTypeChanged(selectEl) {
  var row = selectEl.closest('.tpled-transition-entry');
  if (!row) return;
  var isAsk = selectEl.value === 'ask';
  var hide = isAsk ? 'none' : '';
  row.querySelectorAll('.tpled-tr-action-field, .tpled-tr-target-field').forEach(function(el) { el.style.display = hide; });
  var statusRow = row.querySelector('.tpled-tr-status-row');
  if (statusRow) statusRow.style.display = hide;
  if (isAsk) {
    var tpl = row.querySelector('.tpled-tr-template');
    var tgt = row.querySelector('.tpled-tr-target');
    var st = row.querySelector('.tpled-tr-status');
    var locEnabled = row.querySelector('.tpled-loc-gate-enabled');
    if (tpl) tpl.value = '';
    if (tgt) tgt.value = '';
    if (st) st.value = '';
    if (locEnabled) locEnabled.checked = false;
  }
  _tplSyncLocGateRow(row);
  tplEditorMarkDirty();
}

function tplLocGateToggled(detailsEl) {
  if (!detailsEl) return;
  var row = detailsEl.closest ? detailsEl.closest('.tpled-transition-entry') : null;
  if (row) _tplSyncLocGateRow(row);
}

function tplLocGateEnabledChanged(inputEl) {
  var row = inputEl && inputEl.closest ? inputEl.closest('.tpled-transition-entry') : null;
  if (!row) return;
  if (inputEl.checked) {
    var ship = row.querySelector('.tpled-loc-ship-direct');
    var review = row.querySelector('.tpled-loc-review-above');
    if (ship && !ship.value) ship.value = '50';
    if (review && !review.value) review.value = '150';
  }
  _tplSyncLocGateRow(row);
  tplEditorMarkDirty();
}

function _tplSyncLocGateRow(row) {
  if (!row || !row.querySelectorAll) return;
  var enabled = row.querySelector('.tpled-loc-gate-enabled');
  var isAsk = false;
  var typeEl = row.querySelector('.tpled-tr-type');
  if (typeEl) isAsk = typeEl.value === 'ask';
  var on = !!(enabled && enabled.checked && !isAsk);
  row.querySelectorAll('.tpled-loc-gate-fields input').forEach(function(el) {
    el.disabled = !on;
  });
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
    var targetEl = entries[i].querySelector('.tpled-tr-target');
    var target = targetEl ? (targetEl.value || '').trim() : '';
    var statusEl = entries[i].querySelector('.tpled-tr-status');
    var status = statusEl ? (statusEl.value || '').trim() : '';
    if (type === 'ask') {
      result.push({ ask: true, when: when });
    } else if (tpl) {
      var entry = { action: tpl };
      if (when) entry.when = when;
      if (target) entry.target = target;
      if (status) entry.status = status;
      var locGate = _tplReadTransitionLocGate(entries[i]);
      if (locGate) entry.loc_gate = locGate;
      result.push(entry);
    }
  }
  return result;
}

function _tplReadTransitionLocGate(row) {
  if (!row) return null;
  var enabled = row.querySelector('.tpled-loc-gate-enabled');
  if (!enabled || !enabled.checked) return null;
  function readInt(selector, fallback, label) {
    var el = row.querySelector(selector);
    var raw = el ? String(el.value || '').trim() : '';
    if (raw === '') return fallback;
    var parsed = parseInt(raw, 10);
    if (isNaN(parsed) || parsed < 0) {
      if (el) {
        try { el.focus(); } catch (_e) {}
        if (el.classList) el.classList.add('input-error');
      }
      throw new Error(label + ' must be a non-negative number');
    }
    return parsed;
  }
  return {
    ship_direct_max: readInt('.tpled-loc-ship-direct', 50, 'LOC gate ship direct'),
    review_default_above: readInt('.tpled-loc-review-above', 150, 'LOC gate review threshold'),
    self_review_bypass_allowed: !!(row.querySelector('.tpled-loc-bypass') || {}).checked,
  };
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
  try { return _tplEditorSaveInner(); } catch(e) { _showToast('Save error: ' + e.message, 'error'); }
}
function _tplEditorSaveInner() {
  var name = (document.getElementById('tpled-name').value || '').trim();
  if (!name) {
    document.getElementById('tpled-name').focus();
    return;
  }

  // Sanitize name for filename
  name = name.replace(/[^a-zA-Z0-9_/.-]/g, '-').toLowerCase();

  var labelsRaw = (document.getElementById('tpled-labels').value || '').trim();
  var labels = labelsRaw ? labelsRaw.split(',').map(function(s) { return s.trim(); }).filter(Boolean) : [];

  var prompt = document.getElementById('tpled-prompt').value || '';

  // Validate {{ TASK }} is present
  if (!/\{\{\s*TASK\s*(\|[^}]*)?\}\}/.test(prompt) && !/\{\{\s*torque\.task\.title\s*(\|[^}]*)?\}\}/.test(prompt)) {
    var promptEl = document.getElementById('tpled-prompt');
    if (promptEl) { promptEl.focus(); promptEl.classList.add('input-error'); }
    _showToast('Prompt must contain {{ TASK }}', 'error');
    return;
  }

  var transitions = _tplReadTransitions();
  var agentTemplate = (document.getElementById('tpled-agent-template').value || '').trim();
  var legacyAgent = {
    name_prefix: (document.getElementById('tpled-agent-prefix').value || '').trim(),
    tab_color: (document.getElementById('tpled-agent-color').value || '').trim(),
    command: (document.getElementById('tpled-agent-cmd').value || '').trim(),
    directory: (document.getElementById('tpled-agent-dir').value || '').trim(),
  };

  var actData = {
    description: (document.getElementById('tpled-desc').value || '').trim(),
    group: (document.getElementById('tpled-group').value || '').trim(),
    worktree: document.getElementById('tpled-worktree').checked,
    auto_close_on_done: document.getElementById('tpled-auto-close-on-done').checked,
    disable_role_preamble: document.getElementById('tpled-disable-role-preamble').checked,
    implementation_depth: document.getElementById('tpled-implementation-depth').checked,
    prompt: prompt,
    labels: labels,
    transitions: transitions,
  };
  if (agentTemplate) {
    actData.agent = agentTemplate;
  } else if (legacyAgent.name_prefix || legacyAgent.tab_color
      || legacyAgent.command || legacyAgent.directory) {
    actData.agent = legacyAgent;
  }

  var scope = document.getElementById('tpled-scope').value || 'project';

  var msg = {
    cmd: 'save_action',
    name: name,
    action: actData,
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
  showConfirm('Delete action "' + name + '"?').then(function(yes) {
    if (!yes) return;
    send({ cmd: 'delete_action', name: name, group: _currentGroup() });
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
    agent: (document.getElementById('tpled-agent-template').value || '').trim() || {
      name_prefix: (document.getElementById('tpled-agent-prefix').value || '').trim(),
      tab_color: (document.getElementById('tpled-agent-color').value || '').trim(),
      command: (document.getElementById('tpled-agent-cmd').value || '').trim(),
      directory: (document.getElementById('tpled-agent-dir').value || '').trim(),
    },
    group: (document.getElementById('tpled-group').value || '').trim(),
    worktree: document.getElementById('tpled-worktree').checked,
    auto_close_on_done: document.getElementById('tpled-auto-close-on-done').checked,
    disable_role_preamble: document.getElementById('tpled-disable-role-preamble').checked,
    implementation_depth: document.getElementById('tpled-implementation-depth').checked,
    prompt: document.getElementById('tpled-prompt').value || '',
    labels: labelsRaw ? labelsRaw.split(',').map(function(s) { return s.trim(); }).filter(Boolean) : [],
    transitions: _tplReadTransitions(),
  };
}

/* ---- Pipeline view (canvas with pan/zoom) --------------------------- */

var _plZoom = 1;
var _plPanX = 0;
var _plPanY = 0;
var _plDragging = false;
var _plDragStartX = 0;
var _plDragStartY = 0;
var _plPanStartX = 0;
var _plPanStartY = 0;

function tplSwitchView(view) {
  _tplPanelView = view;
  if (view === 'pipelines') {
    _tplPipelinesData = null;
    _plZoom = 1; _plPanX = 0; _plPanY = 0;
  }
  renderTemplatesPanel();
}

function renderPipelinesView() {
  var el = document.getElementById('tpled-pipelines');
  if (!el) return;

  if (!_tplPipelinesData) {
    el.innerHTML = '<div class="tpled-pipelines-loading">Loading\u2026</div>';
    send({ cmd: 'discover_pipelines', group: _currentGroup() || '' });
    return;
  }

  var pipelines = _tplPipelinesData.pipelines || [];
  if (!pipelines.length) {
    el.innerHTML = '<div class="tpled-pipelines-empty">'
      + 'No pipelines found.<br>'
      + '<span class="dim">Add <code>transitions</code> to your actions.</span></div>';
    return;
  }

  // Merge all pipelines into a single graph
  var allEdges = [];
  var allAsks = [];
  var allActions = {};
  for (var pi = 0; pi < pipelines.length; pi++) {
    var p = pipelines[pi];
    var pActions = p.actions || [];
    for (var ti = 0; ti < pActions.length; ti++) allActions[pActions[ti]] = true;
    for (var ei = 0; ei < p.edges.length; ei++) allEdges.push(p.edges[ei]);
    if (p.asks) for (var ai = 0; ai < p.asks.length; ai++) allAsks.push(p.asks[ai]);
  }
  var actionNames = Object.keys(allActions).sort();

  // Build adjacency
  var adj = {};
  var inCount = {};
  for (var i = 0; i < actionNames.length; i++) {
    adj[actionNames[i]] = [];
    inCount[actionNames[i]] = 0;
  }
  for (var i = 0; i < allEdges.length; i++) {
    var e = allEdges[i];
    if (adj[e.from]) adj[e.from].push(e);
    if (inCount[e.to] !== undefined) inCount[e.to]++;
  }

  // BFS layout: assign layers
  var depths = {};
  var queue = [];
  for (var i = 0; i < actionNames.length; i++) {
    if (inCount[actionNames[i]] === 0) {
      depths[actionNames[i]] = 0;
      queue.push(actionNames[i]);
    }
  }
  if (!queue.length) { // fully cyclic
    depths[actionNames[0]] = 0;
    queue.push(actionNames[0]);
  }
  while (queue.length) {
    var node = queue.shift();
    var edges = adj[node] || [];
    for (var i = 0; i < edges.length; i++) {
      var target = edges[i].to;
      if (depths[target] === undefined) {
        depths[target] = (depths[node] || 0) + 1;
        queue.push(target);
      }
    }
  }
  for (var i = 0; i < actionNames.length; i++) {
    if (depths[actionNames[i]] === undefined) depths[actionNames[i]] = 0;
  }

  // Group by layer
  var maxDepth = 0;
  var byDepth = {};
  for (var name in depths) {
    var d = depths[name];
    if (d > maxDepth) maxDepth = d;
    byDepth[d] = byDepth[d] || [];
    byDepth[d].push(name);
  }

  // Compute node positions
  var NODE_W = 140;
  var NODE_H = 40;
  var ASK_W = 70;
  var ASK_H = 28;
  var ASK_GAP = 10;
  var GAP_X = 40;
  var GAP_Y = 60;
  var nodePos = {}; // name → {x, y}

  // Count back-edges for left channel reservation
  var backEdgeCount = 0;
  for (var i = 0; i < allEdges.length; i++) {
    if ((depths[allEdges[i].to] || 0) <= (depths[allEdges[i].from] || 0)) backEdgeCount++;
  }
  var LEFT_CHANNEL = backEdgeCount ? 30 + backEdgeCount * 16 : 0;

  // Pre-compute which actions have asks (for width calculation)
  var asksForAct = {}; // name → count
  for (var ai = 0; ai < allAsks.length; ai++) {
    var src = allAsks[ai].from;
    asksForAct[src] = (asksForAct[src] || 0) + 1;
  }

  // Calculate layer widths accounting for ask nodes next to their source
  // Each item in a layer is: [NODE_W] + optional [ASK_GAP + ASK_W * askCount]
  // Then GAP_X between items
  function layerItemWidth(name) {
    var w = NODE_W;
    var ac = asksForAct[name] || 0;
    if (ac) w += ASK_GAP + ac * ASK_W + (ac - 1) * 8;
    return w;
  }

  var maxLayerW = 0;
  for (var d = 0; d <= maxDepth; d++) {
    var nodes = byDepth[d] || [];
    var w = 0;
    for (var ni = 0; ni < nodes.length; ni++) {
      if (ni > 0) w += GAP_X;
      w += layerItemWidth(nodes[ni]);
    }
    if (w > maxLayerW) maxLayerW = w;
  }
  maxLayerW = Math.max(maxLayerW, NODE_W);

  // Position nodes: center each layer, ask nodes snug to the right of their source
  var askNodes = [];
  for (var d = 0; d <= maxDepth; d++) {
    var nodes = byDepth[d] || [];
    var layerW = 0;
    for (var ni = 0; ni < nodes.length; ni++) {
      if (ni > 0) layerW += GAP_X;
      layerW += layerItemWidth(nodes[ni]);
    }
    var curX = LEFT_CHANNEL + (maxLayerW - layerW) / 2;
    for (var ni = 0; ni < nodes.length; ni++) {
      if (ni > 0) curX += GAP_X;
      nodePos[nodes[ni]] = {
        x: curX,
        y: d * (NODE_H + GAP_Y)
      };
      curX += NODE_W;
      // Place ask nodes for this template
      var ac = asksForAct[nodes[ni]] || 0;
      if (ac) {
        curX += ASK_GAP;
        var askIdx = 0;
        for (var aai = 0; aai < allAsks.length; aai++) {
          if (allAsks[aai].from === nodes[ni]) {
            askNodes.push({
              id: 'ask-' + nodes[ni] + '-' + askIdx,
              from: nodes[ni],
              when: allAsks[aai].when,
              x: curX,
              y: nodePos[nodes[ni]].y + (NODE_H - ASK_H) / 2
            });
            curX += ASK_W + (askIdx < ac - 1 ? 8 : 0);
            askIdx++;
          }
        }
      }
    }
  }

  // Canvas dimensions
  var maxRight = LEFT_CHANNEL + maxLayerW;
  for (var ai = 0; ai < askNodes.length; ai++) {
    var r = askNodes[ai].x + ASK_W;
    if (r > maxRight) maxRight = r;
  }

  // Pre-count right-side back-edges for canvas width
  var rightBackCount = 0;
  for (var i = 0; i < allEdges.length; i++) {
    var e = allEdges[i];
    if ((depths[e.to] || 0) <= (depths[e.from] || 0)) {
      var fc = (nodePos[e.from] || {}).x || 0;
      var tc = (nodePos[e.to] || {}).x || 0;
      if (tc + NODE_W / 2 >= fc + NODE_W / 2) rightBackCount++;
    }
  }
  var canvasW = maxRight + 60 + rightBackCount * 16 + (rightBackCount ? 30 : 0);
  var canvasH = (maxDepth + 1) * (NODE_H + GAP_Y) - GAP_Y + 80;

  // Build HTML
  el.innerHTML = '<div class="pl-canvas-wrap" id="pl-canvas-wrap">'
    + '<div class="pl-canvas" id="pl-canvas" style="width:' + canvasW + 'px;height:' + canvasH + 'px"></div></div>';

  var canvas = document.getElementById('pl-canvas');

  // SVG for edges
  var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', canvasW);
  svg.setAttribute('height', canvasH);
  svg.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;';

  // Arrow marker
  var defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  var marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
  marker.setAttribute('id', 'pl-arrow');
  marker.setAttribute('viewBox', '0 0 10 10');
  marker.setAttribute('refX', '10');
  marker.setAttribute('refY', '5');
  marker.setAttribute('markerWidth', '8');
  marker.setAttribute('markerHeight', '8');
  marker.setAttribute('orient', 'auto-start-reverse');
  var arrowPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  arrowPath.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
  arrowPath.setAttribute('fill', 'var(--text-dim)');
  marker.appendChild(arrowPath);
  defs.appendChild(marker);
  svg.appendChild(defs);

  // Draw edges
  var PAD = 40; // canvas padding
  var backEdgeLeftIdx = 0;
  var backEdgeRightIdx = 0;
  for (var i = 0; i < allEdges.length; i++) {
    var e = allEdges[i];
    var from = nodePos[e.from];
    var to = nodePos[e.to];
    if (!from || !to) continue;

    var isBackEdge = (depths[e.to] || 0) <= (depths[e.from] || 0);

    var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    var d;
    if (isBackEdge) {
      // Decide side: route through the side closest to the target
      var fromCenterX = from.x + NODE_W / 2;
      var toCenterX = to.x + NODE_W / 2;
      var goRight = toCenterX >= fromCenterX;

      var x1, x2, channelX;
      if (goRight) {
        // Exit right side of source, enter right side of target
        x1 = from.x + NODE_W + PAD;
        x2 = to.x + NODE_W + PAD;
        channelX = maxRight + PAD + 20 + backEdgeRightIdx * 16;
        backEdgeRightIdx++;
      } else {
        // Exit left side of source, enter left side of target
        x1 = from.x + PAD;
        x2 = to.x + PAD;
        channelX = PAD - 20 - backEdgeLeftIdx * 16;
        backEdgeLeftIdx++;
      }
      var y1 = from.y + NODE_H / 2 + PAD;
      var y2 = to.y + NODE_H / 2 + PAD;
      // Two quadratic curves joined at the channel midpoint:
      // 1) source → channel (exits horizontally, turns vertical)
      // 2) channel → target (turns from vertical, arrives horizontally)
      var midY = (y1 + y2) / 2;
      d = 'M ' + x1 + ' ' + y1
        + ' Q ' + channelX + ' ' + y1
        + ' ' + channelX + ' ' + midY
        + ' Q ' + channelX + ' ' + y2
        + ' ' + x2 + ' ' + y2;
      path.setAttribute('stroke-dasharray', '4 3');
    } else {
      // Forward edge: bottom center of source → top center of target
      var x1 = from.x + NODE_W / 2 + PAD;
      var y1 = from.y + NODE_H + PAD;
      var x2 = to.x + NODE_W / 2 + PAD;
      var y2 = to.y + PAD;
      var cy = (y1 + y2) / 2;
      d = 'M ' + x1 + ' ' + y1
        + ' C ' + x1 + ' ' + cy
        + ' ' + x2 + ' ' + cy
        + ' ' + x2 + ' ' + y2;
    }
    path.setAttribute('d', d);
    path.setAttribute('stroke', 'var(--text-dim)');
    path.setAttribute('stroke-width', '1.5');
    path.setAttribute('fill', 'none');
    path.setAttribute('marker-end', 'url(#pl-arrow)');

    svg.appendChild(path);

    // "When" button at edge midpoint
    if (e.when) {
      var mx, my;
      if (isBackEdge) {
        var fromCX = from.x + NODE_W / 2;
        var toCX = to.x + NODE_W / 2;
        var bGoRight = toCX >= fromCX;
        var bx1 = bGoRight ? from.x + NODE_W + PAD : from.x + PAD;
        var by1 = from.y + NODE_H / 2 + PAD;
        var by2 = to.y + NODE_H / 2 + PAD;
        var bChX = bGoRight
          ? maxRight + PAD + 20 + (backEdgeRightIdx - 1) * 16
          : PAD - 20 - (backEdgeLeftIdx - 1) * 16;
        mx = bChX;
        my = (by1 + by2) / 2;
      } else {
        var fx1 = from.x + NODE_W / 2 + PAD;
        var fy1 = from.y + NODE_H + PAD;
        var fx2 = to.x + NODE_W / 2 + PAD;
        var fy2 = to.y + PAD;
        mx = (fx1 + fx2) / 2;
        my = (fy1 + fy2) / 2;
      }
      var whenBtn = document.createElement('div');
      whenBtn.className = 'pl-when-btn';
      whenBtn.style.left = mx + 'px';
      whenBtn.style.top = my + 'px';
      whenBtn.textContent = 'when';
      whenBtn.dataset.when = e.when;
      whenBtn.onclick = function(ev) { ev.stopPropagation(); _plShowWhenTip(this); };
      canvas.appendChild(whenBtn);
    }
  }

  canvas.appendChild(svg);

  // Render node divs
  for (var i = 0; i < actionNames.length; i++) {
    var name = actionNames[i];
    var pos = nodePos[name];
    var outEdges = adj[name] || [];
    var isTerminal = outEdges.length === 0;
    var isEntry = inCount[name] === 0;

    var nodeEl = document.createElement('div');
    nodeEl.className = 'pl-node' + (isTerminal ? ' pl-node-terminal' : '') + (isEntry ? ' pl-node-entry' : '');
    nodeEl.style.cssText = 'left:' + (pos.x + 40) + 'px;top:' + (pos.y + 40) + 'px;width:' + NODE_W + 'px;height:' + NODE_H + 'px;';
    nodeEl.setAttribute('data-tpl', name);
    nodeEl.onclick = (function(n) {
      return function() { tplSwitchView('editor'); tplEditorOnSelect('project:' + n); };
    })(name);
    nodeEl.title = 'Click to edit ' + name;

    var label = document.createElement('div');
    label.className = 'pl-node-label';
    label.textContent = name;
    nodeEl.appendChild(label);

    if (outEdges.length) {
      var sub = document.createElement('div');
      sub.className = 'pl-node-sub';
      sub.textContent = outEdges.length + ' transition' + (outEdges.length > 1 ? 's' : '');
      nodeEl.appendChild(sub);
    } else {
      var sub = document.createElement('div');
      sub.className = 'pl-node-sub dim';
      sub.textContent = 'terminal';
      nodeEl.appendChild(sub);
    }

    canvas.appendChild(nodeEl);
  }

  // Render ask nodes + their edges
  for (var ai = 0; ai < askNodes.length; ai++) {
    var ask = askNodes[ai];
    // Dashed yellow connector from source right edge to ask node left edge
    var srcPos = nodePos[ask.from];
    if (srcPos) {
      var x1 = srcPos.x + NODE_W + PAD;
      var y1 = srcPos.y + NODE_H / 2 + PAD;
      var x2 = ask.x + PAD;
      var y2 = ask.y + ASK_H / 2 + PAD;
      var epath = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      epath.setAttribute('x1', x1);
      epath.setAttribute('y1', y1);
      epath.setAttribute('x2', x2);
      epath.setAttribute('y2', y2);
      epath.setAttribute('stroke', 'var(--text-dim)');
      epath.setAttribute('stroke-width', '1.5');
      epath.setAttribute('stroke-dasharray', '4 3');
      svg.appendChild(epath);
    }

    // Ask node div
    var askEl = document.createElement('div');
    askEl.className = 'pl-node-ask';
    askEl.style.cssText = 'left:' + (ask.x + 40) + 'px;top:' + (ask.y + 40) + 'px;width:' + ASK_W + 'px;height:' + ASK_H + 'px;';
    var askLabel = document.createElement('div');
    askLabel.className = 'pl-node-label';
    askLabel.textContent = '\uD83D\uDC64 ask';
    askEl.appendChild(askLabel);

    if (ask.when) {
      askEl.dataset.when = ask.when;
      askEl.style.cursor = 'pointer';
      askEl.onclick = function(ev) { ev.stopPropagation(); _plShowWhenTip(this); };
    }

    canvas.appendChild(askEl);
  }

  // Apply current transform
  _plApplyTransform();

  // Set up pan/zoom events
  var wrap = document.getElementById('pl-canvas-wrap');
  if (wrap) {
    wrap.onmousedown = _plMouseDown;
    wrap.onwheel = _plWheel;
  }
}

function _plApplyTransform() {
  var canvas = document.getElementById('pl-canvas');
  if (canvas) {
    canvas.style.transform = 'translate(' + _plPanX + 'px,' + _plPanY + 'px) scale(' + _plZoom + ')';
  }
}

function _plShowWhenTip(btn) {
  var existing = document.querySelector('.pl-when-tip');
  if (existing) { existing.remove(); if (existing._src === btn) return; }
  var tip = document.createElement('div');
  tip.className = 'pl-when-tip';
  tip.textContent = btn.dataset.when;
  tip._src = btn;
  document.body.appendChild(tip);
  var r = btn.getBoundingClientRect();
  tip.style.left = Math.max(4, Math.min(r.left + r.width / 2 - tip.offsetWidth / 2, window.innerWidth - tip.offsetWidth - 4)) + 'px';
  tip.style.top = (r.top - tip.offsetHeight - 6) + 'px';
  if (tip.getBoundingClientRect().top < 4) {
    tip.style.top = (r.bottom + 6) + 'px';
  }
  setTimeout(function() {
    function dismiss(ev) {
      if (ev.target === btn) return;
      tip.remove();
      document.removeEventListener('click', dismiss, true);
    }
    document.addEventListener('click', dismiss, true);
  }, 0);
}

function _plMouseDown(e) {
  if (e.target.closest('.pl-when-btn')) return; // let when button clicks through
  if (e.target.closest('.pl-node')) return; // let node clicks through
  e.preventDefault();
  _plDragging = true;
  _plDragStartX = e.clientX;
  _plDragStartY = e.clientY;
  _plPanStartX = _plPanX;
  _plPanStartY = _plPanY;
  document.addEventListener('mousemove', _plMouseMove);
  document.addEventListener('mouseup', _plMouseUp);
}

function _plMouseMove(e) {
  if (!_plDragging) return;
  _plPanX = _plPanStartX + (e.clientX - _plDragStartX);
  _plPanY = _plPanStartY + (e.clientY - _plDragStartY);
  _plApplyTransform();
}

function _plMouseUp() {
  _plDragging = false;
  document.removeEventListener('mousemove', _plMouseMove);
  document.removeEventListener('mouseup', _plMouseUp);
}

function _plWheel(e) {
  e.preventDefault();
  var wrap = document.getElementById('pl-canvas-wrap');
  if (!wrap) return;
  var rect = wrap.getBoundingClientRect();
  var mx = e.clientX - rect.left;
  var my = e.clientY - rect.top;

  var oldZoom = _plZoom;
  var delta = e.deltaY > 0 ? 0.9 : 1.1;
  _plZoom = Math.max(0.2, Math.min(3, _plZoom * delta));

  // Zoom toward cursor
  _plPanX = mx - (mx - _plPanX) * (_plZoom / oldZoom);
  _plPanY = my - (my - _plPanY) * (_plZoom / oldZoom);

  _plApplyTransform();
}

function tplReceivePipelines(msg) {
  _tplPipelinesData = msg;
  if (_tplPanelView === 'pipelines') renderPipelinesView();
}

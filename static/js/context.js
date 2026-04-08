/* ------------------------------------------------------------------ */
/* Context panel app — shared memory browser and note editor            */
/* ------------------------------------------------------------------ */

var _contextFocus = 'group';   // group | task | pipeline | agent
var _contextEntries = [];
var _contextSelectedId = '';
var _contextListLoading = false;
var _contextListError = '';
var _contextSearchQuery = '';
var _contextSearchTimer = null;
var _contextEntryType = '';
var _contextPinnedOnly = false;
var _contextScrollTop = 0;
var _contextLastQueryKey = '';
var _contextSearchHadFocus = false;
var _contextEditor = null;

function _contextCurrentAgent() {
  if (selectedAgentId && state && state.agents && state.agents[selectedAgentId]) {
    return state.agents[selectedAgentId];
  }
  if (typeof focusedItemId !== 'undefined' && focusedItemId
      && state && state.agents && state.agents[focusedItemId]) {
    var focused = state.agents[focusedItemId];
    if (focused.cell_type === 'terminal' && focused.parent_id
        && state.agents[focused.parent_id]) {
      return state.agents[focused.parent_id];
    }
    if (focused.cell_type !== 'terminal') return focused;
  }
  return null;
}

function _contextCurrentTask() {
  if (typeof _boardFocusedTask !== 'undefined' && _boardFocusedTask
      && state && state.board_tasks && state.board_tasks[_boardFocusedTask]) {
    return state.board_tasks[_boardFocusedTask];
  }
  var agent = _contextCurrentAgent();
  if (!agent) return null;
  if (typeof _getAgentTask === 'function') return _getAgentTask(agent.id);
  if (!state || !state.board_tasks) return null;
  for (var id in state.board_tasks) {
    var task = state.board_tasks[id];
    if (task.agent_id === agent.id && task.lane === 'In Progress') return task;
  }
  return null;
}

function _contextCurrentGroup() {
  if (typeof _currentGroup === 'function') return _currentGroup() || '';
  var agent = _contextCurrentAgent();
  return agent ? (agent.group || '') : '';
}

function _contextProjectKey() {
  var agent = _contextCurrentAgent();
  if (!agent) return '';
  return agent.worktree_repo_root || agent.git_root || agent.directory || '';
}

function _contextPipelineRef(task) {
  return task ? (task.pipeline_root_id || task.id || '') : '';
}

function _contextScopeLabel(kind, ref) {
  if (!kind) return 'Unscoped';
  if (kind === 'group') return 'Group: ' + (ref || _contextCurrentGroup() || 'unknown');
  if (kind === 'task') {
    var task = state && state.board_tasks ? state.board_tasks[ref] : null;
    return 'Task: ' + (task ? (task.task || ref) : (ref || 'unknown'));
  }
  if (kind === 'pipeline') {
    var root = state && state.board_tasks ? state.board_tasks[ref] : null;
    return 'Pipeline: ' + (root ? (root.task || ref) : (ref || 'unknown'));
  }
  if (kind === 'project') return 'Project: ' + (ref || _contextProjectKey() || 'unknown');
  return kind + ': ' + (ref || 'unknown');
}

function _contextTargetLabel(kind, ref) {
  if (kind === 'agent') {
    var agent = state && state.agents ? state.agents[ref] : null;
    return agent ? (agent.name || ref) : ref;
  }
  if (kind === 'task') {
    var task = state && state.board_tasks ? state.board_tasks[ref] : null;
    return task ? (task.task || ref) : ref;
  }
  if (kind === 'pipeline') {
    var root = state && state.board_tasks ? state.board_tasks[ref] : null;
    return root ? (root.task || ref) : ref;
  }
  return ref;
}

function _contextFormatTime(ts) {
  if (!ts) return '';
  var d = new Date(ts * 1000);
  var now = Date.now();
  var diffSec = Math.max(0, Math.floor((now - d.getTime()) / 1000));
  if (diffSec < 60) return 'now';
  if (diffSec < 3600) return Math.floor(diffSec / 60) + 'm ago';
  if (diffSec < 86400) return Math.floor(diffSec / 3600) + 'h ago';
  var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var hh = String(d.getHours()).padStart(2, '0');
  var mm = String(d.getMinutes()).padStart(2, '0');
  return months[d.getMonth()] + ' ' + d.getDate() + ', ' + hh + ':' + mm;
}

function _contextPreview(content) {
  var text = String(content || '').trim().replace(/\s+/g, ' ');
  if (text.length <= 180) return text;
  return text.slice(0, 177).trimEnd() + '...';
}

function _contextRetentionLabel(entry) {
  var kind = entry && entry.retention_kind ? String(entry.retention_kind) : '';
  if (kind === 'durable') return 'Durable';
  if (kind === 'transient') return 'Transient';
  if (kind === 'summary' || (entry && entry.synthetic)) return 'Summary';
  return '';
}

function _contextCanMutate(entry) {
  return !!(entry && !entry.synthetic);
}

function _contextDefaultScopeRef(kind) {
  var task = _contextCurrentTask();
  if (kind === 'group') return _contextCurrentGroup();
  if (kind === 'task') return task ? task.id : '';
  if (kind === 'pipeline') return _contextPipelineRef(task);
  if (kind === 'project') return _contextProjectKey();
  return '';
}

function _contextCanFocus(kind) {
  if (kind === 'group') return !!_contextCurrentGroup();
  if (kind === 'task') return !!_contextDefaultScopeRef('task');
  if (kind === 'pipeline') return !!_contextDefaultScopeRef('pipeline');
  if (kind === 'agent') {
    var agent = _contextCurrentAgent();
    return !!(agent && agent.id);
  }
  return false;
}

function _contextBuildListQuery() {
  var group = _contextCurrentGroup();
  var query = {
    cmd: 'memory_list',
    limit: 100,
    entry_type: _contextEntryType || '',
    pinned_only: !!_contextPinnedOnly,
    search: _contextSearchQuery.trim(),
  };
  if (group) query.group_name = group;
  if (_contextFocus === 'task') {
    var taskRef = _contextDefaultScopeRef('task');
    if (taskRef) {
      query.scope_kind = 'task';
      query.scope_ref = taskRef;
    } else if (group) {
      query.scope_kind = 'group';
      query.scope_ref = group;
    }
  } else if (_contextFocus === 'pipeline') {
    var pipelineRef = _contextDefaultScopeRef('pipeline');
    if (pipelineRef) {
      query.scope_kind = 'pipeline';
      query.scope_ref = pipelineRef;
    } else if (group) {
      query.scope_kind = 'group';
      query.scope_ref = group;
    }
  } else if (_contextFocus === 'agent') {
    var agent = _contextCurrentAgent();
    if (agent && agent.id) {
      query.linked_target_kind = 'agent';
      query.linked_target_ref = agent.id;
    }
  } else if (group) {
    query.scope_kind = 'group';
    query.scope_ref = group;
  }
  return query;
}

function _contextSelectedEntry() {
  for (var i = 0; i < _contextEntries.length; i++) {
    if (_contextEntries[i].id === _contextSelectedId) return _contextEntries[i];
  }
  return _contextEntries[0] || null;
}

function _contextRequestEntries(force) {
  var query = _contextBuildListQuery();
  var key = JSON.stringify(query);
  if (!force && key === _contextLastQueryKey) return;
  _contextLastQueryKey = key;
  _contextListLoading = true;
  _contextListError = '';
  if (typeof send === 'function') send(query);
}

function handleContextEntries(msg) {
  _contextListLoading = false;
  _contextListError = '';
  _contextEntries = Array.isArray(msg.entries) ? msg.entries.slice() : [];
  if (_contextSelectedId) {
    var stillSelected = false;
    for (var i = 0; i < _contextEntries.length; i++) {
      if (_contextEntries[i].id === _contextSelectedId) {
        stillSelected = true;
        break;
      }
    }
    if (!stillSelected) _contextSelectedId = '';
  }
  if (!_contextSelectedId && _contextEntries.length) {
    _contextSelectedId = _contextEntries[0].id;
  }
  if (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'context') {
    renderContextPanel();
  }
}

function handleContextEntry(msg) {
  var entry = msg && msg.entry ? msg.entry : null;
  if (!entry) return;
  _contextSelectedId = entry.id;
  _contextListError = '';
  _contextListLoading = false;
  _contextEditor = null;
  _contextRequestEntries(true);
  if (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'context') {
    renderContextPanel();
  }
}

function handleContextError(msg) {
  _contextListLoading = false;
  _contextListError = msg && msg.message ? msg.message : 'Context request failed';
  if (typeof _showToast === 'function' && _contextListError) {
    _showToast(_contextListError, 'error');
  }
  if (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'context') {
    renderContextPanel();
  }
}

function _contextRenderFocusButton(kind, label, enabled) {
  var active = _contextFocus === kind;
  return '<button class="context-focus-btn'
    + (active ? ' active' : '')
    + (!enabled ? ' disabled' : '')
    + '"'
    + (enabled ? ' onclick="contextSetFocus(\'' + kind + '\')"' : '')
    + '>'
    + esc(label)
    + '</button>';
}

function _renderContextEntryCard(entry) {
  var selected = entry.id === _contextSelectedId;
  var title = entry.title || _contextPreview(entry.content) || 'Untitled note';
  var sourceLabel = entry.source_kind === 'manual'
    ? 'Manual note'
    : (entry.source_name || entry.source_id || 'Unknown source');
  var meta = sourceLabel + ' · ' + _contextScopeLabel(entry.scope_kind, entry.scope_ref);
  var links = Array.isArray(entry.links) ? entry.links : [];
  var html = '<div class="context-card'
    + (selected ? ' selected' : '')
    + (entry.pinned ? ' pinned' : '')
    + '" onclick="contextSelectEntry(\'' + entry.id + '\')">';
  html += '<div class="context-card-head">';
  html += '<span class="context-entry-type context-entry-type-' + esc(entry.entry_type || 'note') + '">'
    + esc(entry.entry_type || 'note') + '</span>';
  var retentionLabel = _contextRetentionLabel(entry);
  if (retentionLabel) {
    html += '<span class="context-retention-pill">' + esc(retentionLabel) + '</span>';
  }
  html += '<span class="context-entry-time">' + esc(_contextFormatTime(entry.updated_at || entry.created_at)) + '</span>';
  if (_contextCanMutate(entry)) {
    html += '<button class="context-pin-btn' + (entry.pinned ? ' pinned' : '')
      + '" onclick="event.stopPropagation();contextTogglePin(\'' + entry.id + '\',' + (entry.pinned ? 'false' : 'true') + ')"'
      + ' title="' + (entry.pinned ? 'Unpin entry' : 'Pin entry') + '">'
      + (entry.pinned ? '&#9733;' : '&#9734;')
      + '</button>';
  }
  html += '</div>';
  html += '<div class="context-card-title">' + esc(title) + '</div>';
  html += '<div class="context-card-preview">' + esc(_contextPreview(entry.content)) + '</div>';
  html += '<div class="context-card-meta">' + esc(meta) + '</div>';
  if (links.length) {
    html += '<div class="context-card-links">';
    for (var i = 0; i < links.length; i++) {
      var link = links[i];
      html += '<span class="context-link-chip">' + esc(link.target_kind + ': ' + _contextTargetLabel(link.target_kind, link.target_ref)) + '</span>';
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _renderContextList() {
  if (_contextListLoading && !_contextEntries.length) {
    return '<div class="context-empty">Loading shared context…</div>';
  }
  if (_contextListError && !_contextEntries.length) {
    return '<div class="context-empty">' + esc(_contextListError) + '</div>';
  }
  if (!_contextEntries.length) {
    return '<div class="context-empty">No shared context matches the current filter.</div>';
  }
  var html = '';
  for (var i = 0; i < _contextEntries.length; i++) {
    html += _renderContextEntryCard(_contextEntries[i]);
  }
  return html;
}

function _contextRenderLinks(entry) {
  var links = Array.isArray(entry.links) ? entry.links : [];
  if (!links.length) return '<div class="context-detail-empty">No linked tasks or agents.</div>';
  var html = '<div class="context-link-list">';
  for (var i = 0; i < links.length; i++) {
    var link = links[i];
    html += '<button class="context-link-row"';
    if (link.target_kind === 'agent') {
      html += ' onclick="contextJumpToAgent(\'' + esc(link.target_ref) + '\')"';
    } else if (link.target_kind === 'task' || link.target_kind === 'pipeline') {
      html += ' onclick="contextJumpToTask(\'' + esc(link.target_ref) + '\')"';
    }
    html += '>';
    html += '<span class="context-link-kind">' + esc(link.target_kind) + '</span>';
    html += '<span class="context-link-name">' + esc(_contextTargetLabel(link.target_kind, link.target_ref)) + '</span>';
    html += '</button>';
  }
  html += '</div>';
  return html;
}

function _renderContextDetail(entry) {
  if (_contextEditor) return _renderContextEditor();
  if (!entry) {
    return '<div class="context-detail-empty">Select an entry or create a note.</div>';
  }
  var title = entry.title || 'Untitled note';
  var sourceLabel = entry.source_kind === 'manual'
    ? 'Created manually'
    : 'Source: ' + (entry.source_name || entry.source_id || 'unknown');
  var html = '<div class="context-detail-card">';
  html += '<div class="context-detail-head">';
  html += '<div class="context-detail-badges">';
  html += '<span class="context-entry-type context-entry-type-' + esc(entry.entry_type || 'note') + '">'
    + esc(entry.entry_type || 'note') + '</span>';
  html += '<span class="context-scope-pill">' + esc(_contextScopeLabel(entry.scope_kind, entry.scope_ref)) + '</span>';
  var retentionLabel = _contextRetentionLabel(entry);
  if (retentionLabel) html += '<span class="context-retention-pill">' + esc(retentionLabel) + '</span>';
  if (entry.pinned) html += '<span class="context-pinned-pill">Pinned</span>';
  html += '</div>';
  if (_contextCanMutate(entry)) {
    html += '<button class="context-pin-btn' + (entry.pinned ? ' pinned' : '')
      + '" onclick="contextTogglePin(\'' + entry.id + '\',' + (entry.pinned ? 'false' : 'true') + ')"'
      + ' title="' + (entry.pinned ? 'Unpin entry' : 'Pin entry') + '">'
      + (entry.pinned ? '&#9733;' : '&#9734;')
      + '</button>';
  }
  html += '</div>';
  html += '<div class="context-detail-title">' + esc(title) + '</div>';
  html += '<div class="context-detail-meta">' + esc(sourceLabel) + '</div>';
  html += '<div class="context-detail-meta">Created ' + esc(_contextFormatTime(entry.created_at))
    + ' · Updated ' + esc(_contextFormatTime(entry.updated_at)) + '</div>';
  html += '<div class="context-detail-content">' + formatCode(entry.content || '') + '</div>';
  html += '<div class="context-detail-section-title">Provenance</div>';
  html += '<div class="context-detail-meta">' + esc(entry.source_id || 'No source id') + '</div>';
  html += '<div class="context-detail-section-title">Links</div>';
  html += _contextRenderLinks(entry);
  html += '<div class="context-detail-actions">';
  if (_contextCanMutate(entry)) {
    html += '<button class="btn-secondary btn-sm" onclick="contextEditEntry(\'' + entry.id + '\')">Edit</button>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _renderContextEditor() {
  var editor = _contextEditor;
  var scopeLabel = _contextScopeLabel(editor.scope_kind, editor.scope_ref);
  var html = '<div class="context-editor">';
  html += '<div class="context-detail-title">'
    + esc(editor.mode === 'edit' ? 'Edit Shared Note' : 'New Shared Note')
    + '</div>';
  html += '<label class="context-field-label">Title</label>';
  html += '<input id="context-title-input" class="context-field-input" value="' + esc(editor.title || '') + '"'
    + ' oninput="contextUpdateEditor(\'title\', this.value)">';
  html += '<div class="context-editor-grid">';
  html += '<div><label class="context-field-label">Type</label>';
  html += '<select id="context-type-select" class="context-field-select" onchange="contextUpdateEditor(\'entry_type\', this.value)">';
  html += '<option value="finding"' + (editor.entry_type === 'finding' ? ' selected' : '') + '>Finding</option>';
  html += '<option value="decision"' + (editor.entry_type === 'decision' ? ' selected' : '') + '>Decision</option>';
  html += '<option value="warning"' + (editor.entry_type === 'warning' ? ' selected' : '') + '>Warning</option>';
  html += '<option value="handoff"' + (editor.entry_type === 'handoff' ? ' selected' : '') + '>Handoff</option>';
  html += '<option value="note"' + (editor.entry_type === 'note' ? ' selected' : '') + '>Note</option>';
  html += '</select></div>';
  html += '<div><label class="context-field-label">Scope</label>';
  html += '<select id="context-scope-select" class="context-field-select" onchange="contextSetEditorScope(this.value)">';
  html += '<option value="task"' + (editor.scope_kind === 'task' ? ' selected' : '') + '>Task</option>';
  html += '<option value="pipeline"' + (editor.scope_kind === 'pipeline' ? ' selected' : '') + '>Pipeline</option>';
  html += '<option value="group"' + (editor.scope_kind === 'group' ? ' selected' : '') + '>Group</option>';
  html += '<option value="project"' + (editor.scope_kind === 'project' ? ' selected' : '') + '>Project</option>';
  html += '</select></div>';
  html += '</div>';
  html += '<div class="context-field-note">' + esc(scopeLabel) + '</div>';
  html += '<label class="context-field-label">Content</label>';
  html += '<textarea id="context-content-input" class="context-field-textarea" rows="10"'
    + ' oninput="contextUpdateEditor(\'content\', this.value)">' + esc(editor.content || '') + '</textarea>';
  html += '<label class="context-check"><input type="checkbox"'
    + (editor.pinned ? ' checked' : '')
    + ' onchange="contextUpdateEditor(\'pinned\', this.checked)">'
    + '<span>Pin this entry</span></label>';
  if (editor.mode !== 'edit') {
    var task = _contextCurrentTask();
    var agent = _contextCurrentAgent();
    html += '<div class="context-detail-section-title">Link On Save</div>';
    if (task) {
      html += '<label class="context-check"><input type="checkbox"'
        + (editor.link_task ? ' checked' : '')
        + ' onchange="contextUpdateEditor(\'link_task\', this.checked)">'
        + '<span>Task: ' + esc(task.task || task.id) + '</span></label>';
      html += '<label class="context-check"><input type="checkbox"'
        + (editor.link_pipeline ? ' checked' : '')
        + ' onchange="contextUpdateEditor(\'link_pipeline\', this.checked)">'
        + '<span>Pipeline: ' + esc(_contextTargetLabel('pipeline', _contextPipelineRef(task))) + '</span></label>';
    }
    if (agent) {
      html += '<label class="context-check"><input type="checkbox"'
        + (editor.link_agent ? ' checked' : '')
        + ' onchange="contextUpdateEditor(\'link_agent\', this.checked)">'
        + '<span>Agent: ' + esc(agent.name || agent.id) + '</span></label>';
    }
  }
  html += '<div class="context-detail-actions">';
  html += '<button class="btn-cancel" onclick="contextCancelEditor()">Cancel</button>';
  html += '<button class="btn-primary" onclick="contextSaveEditor()">Save</button>';
  html += '</div>';
  html += '</div>';
  return html;
}

function renderContextPanel() {
  var panel = document.getElementById('panel-context');
  if (!panel) return;
  var panelState = _captureSurfaceState(panel, {
    scrollSelectors: ['#context-list'],
  });
  var queryKey = JSON.stringify(_contextBuildListQuery());
  if (!_contextListLoading && queryKey !== _contextLastQueryKey) {
    _contextRequestEntries(false);
  }
  var group = _contextCurrentGroup();
  var task = _contextCurrentTask();
  var agent = _contextCurrentAgent();
  var summary = [];
  if (group) summary.push('Group: ' + group);
  if (task) summary.push('Task: ' + (task.task || task.id));
  if (agent) summary.push('Agent: ' + (agent.name || agent.id));

  var html = '<div class="context-panel">';
  html += '<div class="context-header">';
  html += '<div><div class="context-title">Context</div><div class="context-subtitle">Shared memory for the current flow</div></div>';
  html += '<div class="context-header-actions">';
  html += '<button class="btn-secondary btn-sm" onclick="contextRefresh(true)">Refresh</button>';
  html += '<button class="btn-secondary btn-sm" onclick="contextOpenCreate()">New Note</button>';
  html += '<button class="btn-primary btn-sm"' + (task ? ' onclick="contextPromoteCurrentTask()"' : ' disabled')
    + '>Promote Task</button>';
  html += '</div></div>';
  html += '<div class="context-focus-row">';
  html += _contextRenderFocusButton('group', 'Group', _contextCanFocus('group'));
  html += _contextRenderFocusButton('task', 'Task', _contextCanFocus('task'));
  html += _contextRenderFocusButton('pipeline', 'Pipeline', _contextCanFocus('pipeline'));
  html += _contextRenderFocusButton('agent', 'Agent Links', _contextCanFocus('agent'));
  html += '</div>';
  html += '<div class="context-toolbar">';
  html += '<input id="context-search-input" class="context-search-input" type="text" placeholder="Search shared context…"'
    + ' value="' + esc(_contextSearchQuery) + '" oninput="contextSearchInput(this.value)">';
  html += '<select class="context-filter-select" onchange="contextSetEntryType(this.value)">';
  html += '<option value="">All types</option>';
  html += '<option value="finding"' + (_contextEntryType === 'finding' ? ' selected' : '') + '>Finding</option>';
  html += '<option value="decision"' + (_contextEntryType === 'decision' ? ' selected' : '') + '>Decision</option>';
  html += '<option value="warning"' + (_contextEntryType === 'warning' ? ' selected' : '') + '>Warning</option>';
  html += '<option value="handoff"' + (_contextEntryType === 'handoff' ? ' selected' : '') + '>Handoff</option>';
  html += '<option value="note"' + (_contextEntryType === 'note' ? ' selected' : '') + '>Note</option>';
  html += '</select>';
  html += '<label class="context-check compact"><input type="checkbox"'
    + (_contextPinnedOnly ? ' checked' : '')
    + ' onchange="contextSetPinnedOnly(this.checked)"><span>Pinned only</span></label>';
  html += '</div>';
  html += '<div class="context-summary">' + esc(summary.join(' · ') || 'No active group or task context') + '</div>';
  html += '<div class="context-browser">';
  html += '<div class="context-list" id="context-list">' + _renderContextList() + '</div>';
  html += '<div class="context-detail">' + _renderContextDetail(_contextSelectedEntry()) + '</div>';
  html += '</div></div>';
  panel.innerHTML = html;

  var listEl = document.getElementById('context-list');
  if (listEl) {
    listEl.scrollTop = _contextScrollTop;
    listEl.addEventListener('scroll', function() {
      _contextScrollTop = listEl.scrollTop;
    });
  }
  if (_contextSearchHadFocus) {
    var input = document.getElementById('context-search-input');
    if (input) {
      input.focus();
      input.selectionStart = input.selectionEnd = input.value.length;
    }
    _contextSearchHadFocus = false;
  }
  _restoreSurfaceState(panel, panelState);
}

function contextRefresh(force) {
  _contextRequestEntries(!!force);
  renderContextPanel();
}

function contextSetFocus(kind) {
  if (!_contextCanFocus(kind)) return;
  _contextFocus = kind;
  _contextSelectedId = '';
  _contextScrollTop = 0;
  _contextLastQueryKey = '';
  contextRefresh(true);
}

function contextSelectEntry(entryId) {
  _contextSelectedId = entryId;
  _contextEditor = null;
  renderContextPanel();
}

function contextSetEntryType(value) {
  _contextEntryType = value || '';
  _contextLastQueryKey = '';
  contextRefresh(true);
}

function contextSetPinnedOnly(checked) {
  _contextPinnedOnly = !!checked;
  _contextLastQueryKey = '';
  contextRefresh(true);
}

function contextSearchInput(value) {
  if (_contextSearchTimer) clearTimeout(_contextSearchTimer);
  _contextSearchTimer = setTimeout(function() {
    _contextSearchQuery = value || '';
    _contextSearchHadFocus = true;
    _contextLastQueryKey = '';
    contextRefresh(true);
  }, 150);
}

function contextTogglePin(entryId, shouldPin) {
  if (typeof send !== 'function') return;
  send({ cmd: shouldPin ? 'memory_pin' : 'memory_unpin', entry_id: entryId });
}

function _contextOpenEditor(editor) {
  _contextEditor = editor;
  renderContextPanel();
}

function contextOpenCreate() {
  var task = _contextCurrentTask();
  var agent = _contextCurrentAgent();
  _contextOpenEditor({
    mode: 'create',
    entry_id: '',
    title: '',
    content: '',
    entry_type: 'note',
    scope_kind: task ? 'task' : 'group',
    scope_ref: task ? task.id : _contextCurrentGroup(),
    pinned: false,
    link_task: !!task,
    link_pipeline: false,
    link_agent: !!agent,
  });
}

function contextPromoteCurrentTask() {
  var task = _contextCurrentTask();
  var agent = _contextCurrentAgent();
  if (!task) return;
  _contextOpenEditor({
    mode: 'create',
    entry_id: '',
    title: task.task || '',
    content: task.description || task.task || '',
    entry_type: 'note',
    scope_kind: 'task',
    scope_ref: task.id,
    pinned: false,
    link_task: true,
    link_pipeline: true,
    link_agent: !!agent,
  });
}

function contextEditEntry(entryId) {
  var entry = null;
  for (var i = 0; i < _contextEntries.length; i++) {
    if (_contextEntries[i].id === entryId) {
      entry = _contextEntries[i];
      break;
    }
  }
  if (!entry) return;
  _contextOpenEditor({
    mode: 'edit',
    entry_id: entry.id,
    title: entry.title || '',
    content: entry.content || '',
    entry_type: entry.entry_type || 'note',
    scope_kind: entry.scope_kind || 'group',
    scope_ref: entry.scope_ref || '',
    pinned: !!entry.pinned,
    link_task: false,
    link_pipeline: false,
    link_agent: false,
  });
}

function contextCancelEditor() {
  _contextEditor = null;
  renderContextPanel();
}

function contextUpdateEditor(field, value) {
  if (!_contextEditor) return;
  _contextEditor[field] = value;
}

function contextSetEditorScope(kind) {
  if (!_contextEditor) return;
  _contextEditor.scope_kind = kind;
  _contextEditor.scope_ref = _contextDefaultScopeRef(kind) || _contextEditor.scope_ref || '';
  renderContextPanel();
}

function contextSaveEditor() {
  if (!_contextEditor || typeof send !== 'function') return;
  var content = String(_contextEditor.content || '').trim();
  if (!content) {
    var textarea = document.getElementById('context-content-input');
    if (textarea) textarea.focus();
    return;
  }
  var payload = {
    cmd: 'memory_publish',
    title: _contextEditor.title || '',
    content: content,
    entry_type: _contextEditor.entry_type || 'note',
    scope_kind: _contextEditor.scope_kind || 'group',
    scope_ref: _contextEditor.scope_ref || '',
    pinned: !!_contextEditor.pinned,
  };
  if (_contextEditor.mode === 'edit') {
    payload.entry_id = _contextEditor.entry_id;
  } else {
    payload.source_kind = 'manual';
    payload.link_targets = [];
    if (_contextEditor.link_task) {
      var task = _contextCurrentTask();
      if (task && task.id) {
        payload.link_targets.push({ target_kind: 'task', target_ref: task.id });
      }
    }
    if (_contextEditor.link_pipeline) {
      var currentTask = _contextCurrentTask();
      var pipelineRef = _contextPipelineRef(currentTask);
      if (pipelineRef) {
        payload.link_targets.push({ target_kind: 'pipeline', target_ref: pipelineRef });
      }
    }
    if (_contextEditor.link_agent) {
      var agent = _contextCurrentAgent();
      if (agent && agent.id) {
        payload.link_targets.push({ target_kind: 'agent', target_ref: agent.id });
      }
    }
  }
  send(payload);
}

function contextJumpToAgent(agentId) {
  if (typeof focusAgent === 'function') focusAgent(agentId);
}

function contextJumpToTask(taskId) {
  if (typeof boardNavigateToTask === 'function') {
    boardNavigateToTask(taskId);
  }
}

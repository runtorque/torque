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
var _contextSplitRatio = 0.38;
var _contextResizeDrag = null;
var _contextCompactDetailOpen = false;
var _contextListRenderFrame = 0;
var _CONTEXT_LIST_VIRTUAL_THRESHOLD = 80;
var _CONTEXT_LIST_ROW_HEIGHT = 112;
var _CONTEXT_LIST_OVERSCAN = 5;
var _CONTEXT_LIST_DEFAULT_VIEWPORT = 520;

function _contextCurrentAgent() {
  var activeGroup = '';
  if (typeof _singleGroupModeEnabled === 'function'
      && _singleGroupModeEnabled()
      && typeof _activeGroup === 'function') {
    activeGroup = _activeGroup() || '';
  }
  if (selectedAgentId && state && state.agents && state.agents[selectedAgentId]) {
    if (!activeGroup || state.agents[selectedAgentId].group === activeGroup) {
      return state.agents[selectedAgentId];
    }
  }
  if (typeof focusedItemId !== 'undefined' && focusedItemId
      && state && state.agents && state.agents[focusedItemId]) {
    var focused = state.agents[focusedItemId];
    if (activeGroup && focused.group !== activeGroup) return null;
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

function _contextClampSplitRatio(value) {
  var ratio = Number(value);
  if (!isFinite(ratio)) return 0.38;
  return Math.min(0.62, Math.max(0.28, ratio));
}

function _contextApplyPersistedSplit() {
  if (state && state.context_panel_split_ratio != null) {
    _contextSplitRatio = _contextClampSplitRatio(state.context_panel_split_ratio);
  }
}

function _contextPanelWidth(panel) {
  if (!panel) return 0;
  if (panel.clientWidth) return panel.clientWidth;
  if (panel.offsetWidth) return panel.offsetWidth;
  if (panel.getBoundingClientRect) {
    var rect = panel.getBoundingClientRect();
    if (rect && rect.width) return rect.width;
  }
  return 0;
}

function _contextUseCompactLayout(panel) {
  return _contextPanelWidth(panel) > 0 && _contextPanelWidth(panel) <= 900;
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
  if (!_contextEditor && !_contextSelectedId) {
    _contextCompactDetailOpen = false;
  }
  if (typeof _panelAppVisible === 'function' ? _panelAppVisible('context') : (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'context')) {
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
  _contextCompactDetailOpen = true;
  _contextRequestEntries(true);
  if (typeof _panelAppVisible === 'function' ? _panelAppVisible('context') : (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'context')) {
    renderContextPanel();
  }
}

function handleContextError(msg) {
  _contextListLoading = false;
  _contextListError = msg && msg.message ? msg.message : 'Context request failed';
  if (typeof _showToast === 'function' && _contextListError) {
    _showToast(_contextListError, 'error');
  }
  if (typeof _panelAppVisible === 'function' ? _panelAppVisible('context') : (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'context')) {
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
  var html = '<div class="context-card ui-card ui-card--comfortable ui-card--interactive'
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

function _contextVirtualRange(total) {
  total = Math.max(0, Number(total) || 0);
  if (total <= _CONTEXT_LIST_VIRTUAL_THRESHOLD) {
    return {
      start: 0,
      end: total,
      before: 0,
      after: 0,
      virtualized: false,
    };
  }
  var viewport = _CONTEXT_LIST_DEFAULT_VIEWPORT;
  var listEl = document.getElementById('context-list');
  if (listEl && typeof listEl.clientHeight === 'number' && listEl.clientHeight > 0) {
    viewport = listEl.clientHeight;
  }
  viewport = Math.max(120, viewport);
  var rawScrollTop = Math.max(0, Number(_contextScrollTop || 0));
  var maxScrollTop = Math.max(0, (total * _CONTEXT_LIST_ROW_HEIGHT) - viewport);
  var scrollTop = Math.min(rawScrollTop, maxScrollTop);
  if (scrollTop !== rawScrollTop) _contextScrollTop = scrollTop;
  var visible = Math.ceil(viewport / _CONTEXT_LIST_ROW_HEIGHT) + (_CONTEXT_LIST_OVERSCAN * 2);
  var start = Math.max(
    0,
    Math.floor(scrollTop / _CONTEXT_LIST_ROW_HEIGHT) - _CONTEXT_LIST_OVERSCAN
  );
  start = Math.min(start, Math.max(0, total - Math.max(1, visible)));
  var end = Math.min(total, start + Math.max(1, visible));
  return {
    start: start,
    end: end,
    before: start * _CONTEXT_LIST_ROW_HEIGHT,
    after: Math.max(0, (total - end) * _CONTEXT_LIST_ROW_HEIGHT),
    virtualized: true,
  };
}

function _contextVirtualSpacer(height) {
  height = Math.max(0, Math.round(Number(height) || 0));
  if (!height) return '';
  return '<div class="context-virtual-spacer" aria-hidden="true" style="height:'
    + height + 'px"></div>';
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
  var range = _contextVirtualRange(_contextEntries.length);
  html += '<div class="context-virtual-list" style="display:flex;flex-direction:column;gap:8px" data-context-virtualized="'
    + (range.virtualized ? 'true' : 'false') + '">';
  html += _contextVirtualSpacer(range.before);
  for (var i = range.start; i < range.end; i++) {
    html += _renderContextEntryCard(_contextEntries[i]);
  }
  html += _contextVirtualSpacer(range.after);
  html += '</div>';
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

function _renderContextDetailPane(compact) {
  var html = '';
  if (compact) {
    html += '<div class="context-detail-nav">';
    html += '<button class="btn-secondary btn-sm" onclick="contextShowList()">Back to List</button>';
    html += '</div>';
  }
  html += _renderContextDetail(_contextSelectedEntry());
  return html;
}

function renderContextPanel() {
  var panel = document.getElementById('panel-context');
  if (!panel) return;
  _contextApplyPersistedSplit();
  var panelState = _captureSurfaceState(panel, {
    scrollSelectors: ['#context-list'],
  });
  var compact = _contextUseCompactLayout(panel);
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
  html += '<div class="context-header-copy"><div class="context-title">Shared Context</div><div class="context-subtitle">Notes and shared memory for the current flow.</div></div>';
  html += '<div class="context-header-actions">';
  html += '<button class="btn-primary btn-sm" onclick="contextOpenCreate()">New Note</button>';
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
  html += '<div class="context-summary">' + esc(summary.join(' · ') || 'Select a group, task, or agent to scope shared memory.') + '</div>';
  html += '<div class="context-browser' + (compact ? ' compact' : ' split') + '" id="context-browser"'
    + (compact ? '' : ' style="--context-list-width:' + Math.round(_contextClampSplitRatio(_contextSplitRatio) * 100) + '%;"')
    + '>';
  if (compact) {
    if (_contextEditor || (_contextCompactDetailOpen && _contextSelectedEntry())) {
      html += '<div class="context-detail context-detail-compact" id="context-detail">' + _renderContextDetailPane(true) + '</div>';
    } else {
      html += '<div class="context-list context-list-compact" id="context-list">' + _renderContextList() + '</div>';
    }
  } else {
    html += '<div class="context-list" id="context-list">' + _renderContextList() + '</div>';
    html += '<div class="context-splitter" onmousedown="contextStartResize(event)" title="Resize context panes"></div>';
    html += '<div class="context-detail" id="context-detail">' + _renderContextDetailPane(false) + '</div>';
  }
  html += '</div></div>';
  panel.innerHTML = html;

  var listEl = document.getElementById('context-list');
  if (listEl) {
    listEl.scrollTop = _contextScrollTop;
    listEl.addEventListener('scroll', function() {
      _contextScrollTop = listEl.scrollTop;
      if (_contextEntries.length > _CONTEXT_LIST_VIRTUAL_THRESHOLD && !_contextListRenderFrame) {
        var scheduler = typeof requestAnimationFrame === 'function'
          ? requestAnimationFrame
          : function(fn) { return setTimeout(fn, 0); };
        _contextListRenderFrame = scheduler(function() {
          _contextListRenderFrame = 0;
          renderContextPanel();
        });
      }
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
  _contextCompactDetailOpen = false;
  contextRefresh(true);
}

function contextSelectEntry(entryId) {
  _contextSelectedId = entryId;
  _contextEditor = null;
  if (_contextUseCompactLayout(document.getElementById('panel-context'))) {
    _contextCompactDetailOpen = true;
  }
  renderContextPanel();
}

function contextSetEntryType(value) {
  _contextEntryType = value || '';
  _contextLastQueryKey = '';
  _contextCompactDetailOpen = false;
  contextRefresh(true);
}

function contextSetPinnedOnly(checked) {
  _contextPinnedOnly = !!checked;
  _contextLastQueryKey = '';
  _contextCompactDetailOpen = false;
  contextRefresh(true);
}

function contextSearchInput(value) {
  if (_contextSearchTimer) clearTimeout(_contextSearchTimer);
  _contextSearchTimer = setTimeout(function() {
    _contextSearchQuery = value || '';
    _contextSearchHadFocus = true;
    _contextLastQueryKey = '';
    _contextCompactDetailOpen = false;
    contextRefresh(true);
  }, 150);
}

function contextTogglePin(entryId, shouldPin) {
  if (typeof send !== 'function') return;
  send({ cmd: shouldPin ? 'memory_pin' : 'memory_unpin', entry_id: entryId });
}

function _contextOpenEditor(editor) {
  _contextEditor = editor;
  _contextCompactDetailOpen = true;
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
  if (!_contextSelectedEntry()) _contextCompactDetailOpen = false;
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

function contextShowList() {
  _contextCompactDetailOpen = false;
  _contextEditor = null;
  renderContextPanel();
}

function contextStartResize(event) {
  var panel = document.getElementById('panel-context');
  var browser = document.getElementById('context-browser');
  if (!panel || !browser || _contextUseCompactLayout(panel)) return;
  var rect = browser.getBoundingClientRect ? browser.getBoundingClientRect() : null;
  _contextResizeDrag = {
    left: rect && isFinite(rect.left) ? rect.left : 0,
    width: Math.max(1, rect && isFinite(rect.width) && rect.width ? rect.width : (_contextPanelWidth(browser) || _contextPanelWidth(panel) || 1)),
  };
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  if (document && typeof document.addEventListener === 'function') {
    document.addEventListener('mousemove', contextDragResize);
    document.addEventListener('mouseup', contextStopResize);
  }
  contextDragResize(event);
}

function contextDragResize(event) {
  if (!_contextResizeDrag) return;
  var clientX = event && typeof event.clientX === 'number'
    ? event.clientX
    : (_contextResizeDrag.left + (_contextResizeDrag.width * _contextSplitRatio));
  _contextSplitRatio = _contextClampSplitRatio((clientX - _contextResizeDrag.left) / _contextResizeDrag.width);
  var browser = document.getElementById('context-browser');
  if (browser && browser.style) {
    var width = Math.round(_contextSplitRatio * 100) + '%';
    if (typeof browser.style.setProperty === 'function') {
      browser.style.setProperty('--context-list-width', width);
    } else {
      browser.style['--context-list-width'] = width;
    }
  }
}

function contextStopResize() {
  if (!_contextResizeDrag) return;
  if (document && typeof document.removeEventListener === 'function') {
    document.removeEventListener('mousemove', contextDragResize);
    document.removeEventListener('mouseup', contextStopResize);
  }
  _contextResizeDrag = null;
  if (state) state.context_panel_split_ratio = _contextSplitRatio;
  if (typeof send === 'function') {
    send({ cmd: 'ui_set_context_panel_split', ratio: _contextSplitRatio });
  }
}

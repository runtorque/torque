function _worktreePrStateLabel(pr) {
  const state = _worktreePrNormalizeState(pr && pr.state, pr && pr.pending);
  const labels = {
    auto_merge_enabled: 'Auto-merge pending',
    open: 'PR open',
    blocked: 'PR blocked',
    merged: 'PR merged',
    closed: 'PR closed',
    draft: 'PR draft',
  };
  if (labels[state]) return labels[state];
  if (!state && pr && (pr.url || pr.number !== '')) return 'PR open';
  if (!state) return '';
  return 'PR ' + state.replace(/[_-]+/g, ' ');
}

function _worktreePrStateClass(pr) {
  const state = _worktreePrNormalizeState(pr && pr.state, pr && pr.pending);
  if (state === 'auto_merge_enabled') return 'pending';
  if (state === 'merged') return 'merged';
  if (state === 'blocked' || state === 'closed') return 'blocked';
  if (state === 'open' || state === 'draft') return 'open';
  return 'unknown';
}

function _worktreePrLinkLabel(pr) {
  if (pr && pr.number !== '' && pr.number != null) return '#' + pr.number;
  return 'Pull request';
}

function _renderWorktreePrInline(pr) {
  if (!pr || typeof pr !== 'object') return '';
  if (!pr.url && pr.number === '' && !pr.state && !pr.merge_state && !pr.head_sha) return '';
  const label = _worktreePrLinkLabel(pr);
  const stateLabel = _worktreePrStateLabel(pr);
  let html = '<span class="detail-pr-inline">';
  if (pr.url) {
    html += '<a class="detail-pr-link" href="' + esc(pr.url)
      + '" target="_blank" rel="noopener noreferrer"'
      + ' onclick="event.stopPropagation()" title="' + esc(pr.url) + '">'
      + esc(label) + '</a>';
  } else {
    html += '<span class="detail-pr-link-muted">' + esc(label) + '</span>';
  }
  if (stateLabel) {
    const cls = _worktreePrStateClass(pr);
    html += '<span class="detail-wt-tag detail-pr-state detail-pr-state-'
      + esc(cls) + '">' + esc(stateLabel) + '</span>';
  }
  html += '</span>';
  return html;
}

var _agentDetailUiState = {};

function _agentDetailState(agentId) {
  const key = String(agentId || '');
  if (!_agentDetailUiState[key]) {
    _agentDetailUiState[key] = {
      task_expanded: false,
      expanded_messages: {},
      description_editor: {
        task_id: '',
        open: false,
        draft: '',
      },
    };
  }
  return _agentDetailUiState[key];
}

function _agentDetailDescriptionState(agentId, task) {
  const detailState = _agentDetailState(agentId);
  const taskId = String((task && task.id) || '');
  const currentDescription = String((task && task.description) || '');
  if (!detailState.description_editor || detailState.description_editor.task_id !== taskId) {
    detailState.description_editor = {
      task_id: taskId,
      open: false,
      draft: currentDescription,
    };
  } else if (!detailState.description_editor.open) {
    detailState.description_editor.draft = currentDescription;
  }
  return detailState.description_editor;
}

function _agentDetailMessageKey(message, index) {
  const action = String((message && message.action) || '');
  const ts = Number((message && message.timestamp) || 0);
  if (ts > 0) return `mcp:${ts}:${action}`;
  return `mcp:${index}:${action}`;
}

function _toggleAgentDetailTask(agentId) {
  const detailState = _agentDetailState(agentId);
  detailState.task_expanded = !detailState.task_expanded;
  // In compact mode the linked task card omits description/artifacts. Fire
  // a hydrate when the panel is opening so the expanded body shows real
  // description text instead of a false "Add description" placeholder.
  if (detailState.task_expanded
      && typeof _compactModeActive === 'function'
      && _compactModeActive()
      && typeof ensureTaskDetail === 'function') {
    const task = _getAgentTask(agentId);
    const taskId = task ? String(task.id || '') : '';
    if (taskId
        && typeof _compactTaskHasFullDetail === 'function'
        && !_compactTaskHasFullDetail(task)) {
      ensureTaskDetail(taskId, function() { render(); });
    }
  }
  render();
}

function _toggleAgentDetailMessage(agentId, messageKey) {
  const state = _agentDetailState(agentId);
  if (state.expanded_messages[messageKey]) delete state.expanded_messages[messageKey];
  else state.expanded_messages[messageKey] = true;
  render();
}

function agentDetailEditDescription(agentId, taskId) {
  const task = state && state.board_tasks ? state.board_tasks[taskId] : null;
  if (!task) return;
  // Hydrate before opening the editor so the draft isn't seeded from an
  // empty compact card — otherwise Save could overwrite an existing
  // server-side description with whatever the user typed on top of "".
  if (typeof _compactModeActive === 'function'
      && _compactModeActive()
      && typeof _compactTaskHasFullDetail === 'function'
      && !_compactTaskHasFullDetail(task)
      && typeof ensureTaskDetail === 'function') {
    ensureTaskDetail(taskId, function() {
      agentDetailEditDescription(agentId, taskId);
    });
    return;
  }
  const editor = _agentDetailDescriptionState(agentId, task);
  editor.open = true;
  editor.draft = String(task.description || '');
  render();
  requestAnimationFrame(function() {
    const input = document.getElementById('detail-description-input');
    if (!input) return;
    if (typeof input.focus === 'function') input.focus();
    if ('value' in input && 'selectionStart' in input) {
      const cursor = String(input.value || '').length;
      input.selectionStart = cursor;
      input.selectionEnd = cursor;
    }
  });
}

function agentDetailDescriptionInput(agentId, taskId, value) {
  const task = state && state.board_tasks ? state.board_tasks[taskId] : null;
  if (!task) return;
  const editor = _agentDetailDescriptionState(agentId, task);
  editor.open = true;
  editor.draft = String(value || '');
}

function agentDetailCancelDescriptionEdit(agentId, taskId) {
  const task = state && state.board_tasks ? state.board_tasks[taskId] : null;
  if (!task) return;
  const editor = _agentDetailDescriptionState(agentId, task);
  editor.open = false;
  editor.draft = String(task.description || '');
  render();
}

function agentDetailSaveDescription(agentId, taskId) {
  const task = state && state.board_tasks ? state.board_tasks[taskId] : null;
  if (!task) return;
  const editor = _agentDetailDescriptionState(agentId, task);
  const input = document.getElementById('detail-description-input');
  if (input && 'value' in input) editor.draft = input.value;
  // Defence in depth: never issue a destructive update against a card
  // whose previous description we never actually loaded.
  if (typeof _compactModeActive === 'function'
      && _compactModeActive()
      && typeof _compactTaskHasFullDetail === 'function'
      && !_compactTaskHasFullDetail(task)
      && typeof ensureTaskDetail === 'function') {
    const pendingDraft = String(editor.draft || '');
    ensureTaskDetail(taskId, function() {
      const refreshed = state && state.board_tasks ? state.board_tasks[taskId] : null;
      if (!refreshed) return;
      const refreshedEditor = _agentDetailDescriptionState(agentId, refreshed);
      refreshedEditor.open = true;
      refreshedEditor.draft = pendingDraft;
      agentDetailSaveDescription(agentId, taskId);
    });
    return;
  }
  const previousDescription = String(task.description || '');
  const nextDescription = String(editor.draft || '').trim();
  editor.open = false;
  editor.draft = nextDescription;
  if (nextDescription !== previousDescription) {
    task.description = nextDescription;
    send({ cmd: 'board_update_task', id: taskId, description: nextDescription });
  }
  render();
}

function agentDetailDescriptionKeydown(evt, agentId, taskId) {
  if (!evt) return;
  if (evt.key === 'Escape') {
    evt.preventDefault();
    evt.stopPropagation();
    agentDetailCancelDescriptionEdit(agentId, taskId);
  } else if (evt.key === 'Enter' && (evt.metaKey || evt.ctrlKey)) {
    evt.preventDefault();
    evt.stopPropagation();
    agentDetailSaveDescription(agentId, taskId);
  }
}

function _detailMultilineHtml(text) {
  return formatCode(text || '').replace(/\n/g, '<br>');
}

function _detailTaskLinkHtml(taskId) {
  if (!taskId) return '';
  return '<button type="button" class="detail-link-arrow"'
    + ' title="View task on board"'
    + ' data-focus-key="detail-task-link:' + esc(taskId) + '"'
    + ' onclick="event.stopPropagation();if(typeof boardNavigateToTask===\'function\'){boardNavigateToTask(\''
    + esc(taskId) + '\');}">\u2192</button>';
}

function _architectPendingHiresForAgent(agentId) {
  if (!state || !state.pending_hires) return [];
  const architectId = String(agentId || '');
  return Object.values(state.pending_hires).filter(function(hire) {
    return String((hire && hire.architect_id) || '') === architectId;
  }).sort(function(a, b) {
    const aTs = Number((a && (a.created_at || a.updated_at)) || 0);
    const bTs = Number((b && (b.created_at || b.updated_at)) || 0);
    if (aTs !== bTs) return bTs - aTs;
    return String((a && a.id) || '').localeCompare(String((b && b.id) || ''));
  });
}

function _renderAgentDetailDescription(agentId, task) {
  if (!task) return '';
  const editor = _agentDetailDescriptionState(agentId, task);
  const title = task.description ? 'Edit task description' : 'Add task description';
  const agentIdJs = JSON.stringify(String(agentId || ''));
  const taskIdJs = JSON.stringify(String(task.id || ''));
  let html = '<div class="detail-expand-description-wrap">';
  if (editor.open) {
    html += `<textarea id="detail-description-input" class="detail-inline-description-input" rows="3"`
      + ` data-focus-key="detail-description-input:${esc(task.id || '')}"`
      + ` placeholder="Add task description..."`
      + ` oninput='agentDetailDescriptionInput(${agentIdJs},${taskIdJs},this.value)'`
      + ` onkeydown='agentDetailDescriptionKeydown(event,${agentIdJs},${taskIdJs})'>${esc(editor.draft || '')}</textarea>`;
    html += `<div class="detail-inline-editor-actions">`;
    html += `<button type="button" id="detail-description-save-btn" class="detail-inline-editor-btn detail-inline-editor-btn-primary"`
      + ` data-focus-key="detail-description-save:${esc(task.id || '')}"`
      + ` onclick='event.stopPropagation();agentDetailSaveDescription(${agentIdJs},${taskIdJs})'>Save</button>`;
    html += `<button type="button" id="detail-description-cancel-btn" class="detail-inline-editor-btn"`
      + ` data-focus-key="detail-description-cancel:${esc(task.id || '')}"`
      + ` onclick='event.stopPropagation();agentDetailCancelDescriptionEdit(${agentIdJs},${taskIdJs})'>Cancel</button>`;
    html += `</div>`;
  } else {
    html += `<div class="detail-expand-description-row">`;
    if (task.description) {
      html += `<div class="detail-expand-description">${_detailMultilineHtml(task.description)}</div>`;
    } else {
      html += `<div class="detail-expand-description detail-expand-description-empty">Add description</div>`;
    }
    html += `<button type="button" id="detail-description-edit-btn" class="detail-description-edit"`
      + ` title="${esc(title)}"`
      + ` aria-label="${esc(title)}"`
      + ` data-focus-key="detail-description-edit:${esc(task.id || '')}"`
      + ` onclick='event.stopPropagation();agentDetailEditDescription(${agentIdJs},${taskIdJs})'>&#x270E;</button>`;
    html += `</div>`;
  }
  html += `</div>`;
  return html;
}

function _taskPreservedMergeDiffArtifact(task, branch) {
  const artifacts = task && Array.isArray(task.artifacts) ? task.artifacts : [];
  for (let i = 0; i < artifacts.length; i++) {
    const artifact = artifacts[i] || {};
    const metadata = artifact.metadata || {};
    if (artifact.type !== 'diff' || !metadata.preserved_on_merge) continue;
    if (branch && metadata.worktree_branch && metadata.worktree_branch !== branch) continue;
    return artifact;
  }
  return null;
}

function _preservedMergeDiffForAgent(agent) {
  if (!agent || !state || !state.board_tasks) return null;
  const repoRoot = agent.worktree_repo_root || agent.git_root || '';
  const branch = agent.worktree_branch || '';
  const branchKey = repoRoot && branch ? (repoRoot + '::' + branch) : '';
  // worktree_boundary is eager in compact-v1; filter down to tasks whose
  // boundary matches this agent's branch and hydrate only those so the
  // lazy artifacts list resolves for the clickable "merged" badge.
  if (typeof _compactHydrateTasksMatching === 'function' && branchKey) {
    _compactHydrateTasksMatching(function(t) {
      return !!t && _taskBoundaryBranchKey(t) === branchKey;
    });
  }
  let winner = null;
  let winnerArtifact = null;
  let winnerSort = '';
  for (const task of Object.values(state.board_tasks)) {
    if (!task) continue;
    const artifact = _taskPreservedMergeDiffArtifact(task, branch);
    if (!artifact) continue;
    const taskBranchKey = _taskBoundaryBranchKey(task);
    if (branchKey && taskBranchKey && taskBranchKey !== branchKey) continue;
    const boundary = _taskBoundaryMeta(task);
    const metadata = artifact.metadata || {};
    const sortValue = String(
      boundary.merged_at
      || metadata.boundary_recorded_at
      || _taskBoundarySortValue(task)
      || task.updated_at
      || task.id
      || ''
    );
    if (!winner || sortValue > winnerSort) {
      winner = task;
      winnerArtifact = artifact;
      winnerSort = sortValue;
    }
  }
  if (!winner || !winnerArtifact) return null;
  return { task: winner, artifact: winnerArtifact };
}

function _captureAgentDetailDrafts() {
  if (typeof selectedAgentId === 'undefined' || !selectedAgentId) return;
  const task = _getAgentTask(selectedAgentId);
  if (!task) return;
  const editor = _agentDetailDescriptionState(selectedAgentId, task);
  if (!editor.open) return;
  const input = document.getElementById('detail-description-input');
  if (input && 'value' in input) editor.draft = input.value;
}

function renderAgentDetails(a) {
  const statusCls = agentStatusClass(a);
  const typeInfo = a.agent_type ? (AGENT_TYPE_LABELS[a.agent_type] || { label: a.agent_type }) : null;
  const detailState = _agentDetailState(a.id);
  const isArchitect = (a.kind || '') === 'architect';

  let h = `<div class="agent-details">`;
  h += `<div class="detail-hdr">`;
  h += `  <span class="detail-name">${esc(a.name)}</span>`;
  if (typeInfo && typeInfo.label) {
    h += `  <span class="detail-type">${esc(typeInfo.label)}</span>`;
  }
  h += `  <span class="detail-status ${statusCls}">`;
  if (statusCls === 'attention') h += esc(a.error_message || 'Needs attention');
  else if (statusCls === 'working') h += 'Working';
  else if (statusCls === 'idle') h += 'Idle';
  else if (statusCls === 'disconnected') h += 'Stopped';
  h += `</span>`;
  h += `</div>`;

  /* Linked task */
  const _dt = _getAgentTask(a.id);
  if (_dt) {
    const taskExpanded = !!detailState.task_expanded;
    h += `<div class="detail-row detail-row-task${taskExpanded ? ' detail-row-expanded' : ''}"><span class="detail-label">Task</span><div class="detail-val detail-val-stack">`;
    h += `<div class="detail-inline-actions">`;
    h += `<button type="button" class="detail-inline-toggle" title="${esc(taskExpanded ? 'Collapse task details' : 'Expand task details')}" data-focus-key="detail-task-toggle:${esc(a.id)}" onclick="_toggleAgentDetailTask('${esc(a.id)}')">`;
    h += `<span class="detail-task-summary" title="${esc(_dt.task)}">${formatCode(_dt.task)}</span>`;
    if (_dt.action_name) {
      h += `<span class="detail-task-action">${esc(_dt.action_name)}</span>`;
    }
    if (_dt.status) {
      h += `<span class="detail-task-status">${esc(_dt.status)}</span>`;
    } else if (_dt.lane) {
      h += `<span class="detail-task-lane">${esc(_dt.lane)}</span>`;
    }
    h += `<span class="detail-expand-caret">${taskExpanded ? '\u25BE' : '\u25B8'}</span>`;
    h += `</button>`;
    h += _detailTaskLinkHtml(_dt.id || '');
    h += `</div>`;
    if (taskExpanded) {
      h += `<div class="detail-expand-body">`;
      h += `<div class="detail-expand-title">${_detailMultilineHtml(_dt.task)}</div>`;
      h += _renderAgentDetailDescription(a.id, _dt);
      h += `</div>`;
    }
    h += `</div></div>`;
  }

  const boundaryOverview = _branchBoundaryOverviewForAgent(a);
  if (boundaryOverview && boundaryOverview.latest_boundary_task) {
    const boundaryTask = boundaryOverview.latest_boundary_task;
    const boundaryBadge = boundaryOverview.branch_advanced
      ? 'Branch advanced'
      : 'Safe review point';
    h += `<div class="detail-row"><span class="detail-label">Review point</span><span class="detail-val detail-task" title="${esc(boundaryTask.task)}">${formatCode(boundaryTask.task)}<span class="detail-task-status">${esc(boundaryBadge)}</span></span></div>`;
    const prHtml = _renderWorktreePrInline(
      _worktreePrMetadataFromBoundary(_taskBoundaryMeta(boundaryTask))
    );
    if (prHtml) {
      h += `<div class="detail-row"><span class="detail-label">PR</span><span class="detail-val detail-pr">${prHtml}</span></div>`;
    }
    if (boundaryOverview.queued_followers.length) {
      h += `<div class="detail-row"><span class="detail-label">Queued next</span><span class="detail-val">${esc(boundaryOverview.queued_followers.map(function(task) { return task.task; }).join(', '))}</span></div>`;
    }
    if (boundaryOverview.started_followers.length) {
      h += `<div class="detail-row"><span class="detail-label">Beyond boundary</span><span class="detail-val">${esc(boundaryOverview.started_followers.map(function(task) { return task.task; }).join(', '))}</span></div>`;
    }
  }

  /* MCP Messages */
  if (a.mcp_messages && a.mcp_messages.length) {
    const icons = { progress: '\u25CF', done: '\u2714', ready: '\u2714', blocked: '\u26D4', error: '\u2716', derive: '\u2934', ask: '\u2753', name: '\u270E' };
    h += `<div class="detail-row detail-row-mcp"><span class="detail-label">Messages</span>`;
    h += `<div class="mcp-log">`;
    const msgs = a.mcp_messages.slice(0, 20);
    for (let i = 0; i < msgs.length; i++) {
      const m = msgs[i];
      const ico = icons[m.action] || '\u25CF';
      const ago = _relativeTime(m.timestamp);
      const messageKey = _agentDetailMessageKey(m, i);
      const messageExpanded = !!detailState.expanded_messages[messageKey];
      h += `<div class="mcp-entry-wrap${messageExpanded ? ' expanded' : ''}">`;
      h += `<button type="button" class="mcp-entry mcp-entry-toggle mcp-${esc(m.action)}${m.message ? ' mcp-clickable' : ''}" data-focus-key="detail-message:${esc(a.id)}:${esc(messageKey)}"`;
      if (m.message) h += ` title="${esc(messageExpanded ? 'Collapse message' : 'Expand message')}" onclick="_toggleAgentDetailMessage('${esc(a.id)}','${esc(messageKey)}')"`;
      h += `><span class="mcp-icon">${ico}</span><span class="mcp-text">${esc(m.message)}</span><span class="mcp-time">${esc(ago)}</span></button>`;
      if (m.message && messageExpanded) {
        h += `<div class="mcp-entry-expanded">${_detailMultilineHtml(m.message)}</div>`;
      }
      h += `</div>`;
    }
    h += `</div></div>`;
  }

  if (isArchitect) {
    const pendingHires = _architectPendingHiresForAgent(a.id);
    if (pendingHires.length) {
      h += `<div class="detail-section"><div class="detail-section-head"><span class="detail-section-title">Pending hires</span><span class="detail-section-count ui-badge ui-badge--micro ui-badge--warning ui-badge--count">${pendingHires.length}</span></div><div class="detail-section-list">`;
      for (let i = 0; i < pendingHires.length; i++) {
        const hire = pendingHires[i] || {};
        const hireIdJs = JSON.stringify(String(hire.id || ''));
        const summaryParts = [];
        if (hire.requested_provider) summaryParts.push(String(hire.requested_provider));
        if (hire.requested_command) summaryParts.push(String(hire.requested_command));
        if (hire.requested_directory) summaryParts.push(String(hire.requested_directory));
        h += `<div class="detail-section-card">`;
        h += `<div class="detail-section-card-head"><span class="detail-section-primary" title="${esc(hire.requested_name || '')}">${esc(hire.requested_name || 'Engineer')}</span><span class="detail-task-status">pending</span></div>`;
        if (summaryParts.length) {
          const summary = summaryParts.join(' • ');
          h += `<div class="detail-section-card-meta" title="${esc(summary)}">${esc(summary)}</div>`;
        }
        if (hire.created_at) {
          h += `<div class="detail-section-card-meta">Requested ${esc(_relativeTime(hire.created_at))}</div>`;
        }
        h += `<div class="detail-section-card-actions">`;
        h += `<button type="button" class="detail-inline-editor-btn detail-inline-editor-btn-primary" data-focus-key="detail-pending-hire-approve:${esc(hire.id || '')}" onclick='event.stopPropagation();approvePendingHire(${hireIdJs})'>Approve</button>`;
        h += `<button type="button" class="detail-inline-editor-btn" data-focus-key="detail-pending-hire-reject:${esc(hire.id || '')}" onclick='event.stopPropagation();rejectPendingHire(${hireIdJs})'>Reject</button>`;
        h += `</div></div>`;
      }
      h += `</div></div>`;
    }
  }

  /* Branch — worktree branch takes priority, then regular git branch */
  if (a.worktree_branch) {
    const branch = a.worktree_branch.replace(/^torque\//, '');
    let branchExtra = '';
    if (a.worktree_merged) {
      const preservedDiff = _preservedMergeDiffForAgent(a);
      if (preservedDiff && preservedDiff.task && preservedDiff.artifact) {
        branchExtra += ' <button type="button" class="detail-wt-tag detail-wt-merged detail-wt-tag-button"'
          + ` title="View preserved merge diff" data-focus-key="detail-merged-diff:${esc(preservedDiff.task.id)}:${esc(preservedDiff.artifact.id || '')}"`
          + ` data-task-id="${esc(preservedDiff.task.id)}"`
          + ` data-artifact-id="${esc(preservedDiff.artifact.id || '')}"`
          + ` data-artifact-filename="${esc(preservedDiff.artifact.filename || '')}"`
          + ` data-artifact-path="${esc((preservedDiff.artifact.path || ((preservedDiff.artifact.storage || {}).path) || ''))}"`
          + ' onclick="event.stopPropagation();if(typeof openTaskArtifactById===\'function\'){openTaskArtifactById(this.dataset.taskId,this.dataset.artifactId,this.dataset.artifactFilename,this.dataset.artifactPath);}">merged</button>';
      } else {
        branchExtra += ' <span class="detail-wt-tag detail-wt-merged">merged</span>';
      }
    } else {
      branchExtra += ' <span class="detail-wt-tag">worktree</span>';
    }
    const behind = a.worktree_behind || 0;
    const ahead = a.worktree_ahead || 0;
    if (behind || ahead) {
      let parts = [];
      if (ahead) parts.push(`<span class="detail-ahead">\u2191${ahead}</span>`);
      if (behind) parts.push(`<span class="detail-behind">\u2193${behind}</span>`);
      branchExtra += ' ' + parts.join(' ');
    }
    h += `<div class="detail-row"><span class="detail-label">Branch</span><span class="detail-val detail-branch">\u2387 ${esc(branch)}${branchExtra}</span></div>`;
    const diff = a.worktree_diff || {};
    if (diff.files) {
      h += `<button type="button" class="detail-row detail-row-button detail-row-clickable" data-focus-key="detail-diff:${esc(a.id)}" title="View branch diff" onclick="event.stopPropagation();if(typeof showDiffView==='function'){showDiffView('${esc(a.id)}',true);}"><span class="detail-label">Changes</span><span class="detail-val">${diff.files} file${diff.files !== 1 ? 's' : ''} <span class="detail-ins">+${diff.insertions || 0}</span> <span class="detail-del">-${diff.deletions || 0}</span></span></button>`;
    }
    if (a.worktree_checkpoints > 0) {
      h += `<button type="button" class="detail-row detail-row-button detail-row-clickable" data-focus-key="detail-history:${esc(a.id)}" title="Open checkpoint history" onclick="event.stopPropagation();if(typeof worktreeHistory==='function'){worktreeHistory('${esc(a.id)}');}"><span class="detail-label">Checkpoints</span><span class="detail-val">${a.worktree_checkpoints}</span></button>`;
    }
  } else if (a.current_branch) {
    h += `<div class="detail-row"><span class="detail-label">Branch</span><span class="detail-val detail-branch">\u2387 ${esc(a.current_branch)}</span></div>`;
  }

  /* Directory */
  const directoryPath = a.current_path || a.directory || '';
  if (directoryPath) {
    const dir = _formatDisplayPath(directoryPath, a.git_root || a.worktree_repo_root || '');
    h += `<div class="detail-row"><span class="detail-label">Directory</span><span class="detail-val detail-dir" title="${esc(directoryPath)}">${esc(dir)}</span></div>`;
  }

  /* Last event */
  if (a.last_event_at > 0) {
    const ago = _relativeTime(a.last_event_at);
    const showAgo = ((Date.now() / 1000) - a.last_event_at) > 30;
    if (a.last_event_text) {
      h += `<div class="detail-row detail-row-event"><span class="detail-label">Last event</span><span class="detail-val detail-last-event">${esc(a.last_event_text)}${showAgo ? ` <span class="detail-time">(${esc(ago)})</span>` : ''}</span></span></div>`;
    } else {
      h += `<div class="detail-row"><span class="detail-label">Last event</span><span class="detail-val">${esc(ago)}</span></div>`;
    }
  }

  h += `</div>`;
  return h;
}

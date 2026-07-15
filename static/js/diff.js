/* Full-panel worktree diff review view */

var _diffViewOpen = false;
var _diffViewAgentId = '';
var _diffViewData = null;
var _diffCollapsedFiles = {};  // path -> 'collapsed' | 'expanded' override
var _diffCollapseAllFiles = false;
var _diffCollapseInitialized = false;
var _diffRenderedLineLimits = {}; // path -> visible line budget
var _diffMergeCheck = null;   // null = loading, {clean, dirty, conflicts}
var _diffCommitMsg = '';       // editable commit message
var _diffMerging = false;      // true while merge request in flight
var _diffMergeProgress = null; // {phase, message} while create+merge runs
var _diffReadOnly = false;     // true when opened from "View Diff" (no merge controls)
var DIFF_AUTO_COLLAPSE_FILE_THRESHOLD = 12;
var DIFF_AUTO_COLLAPSE_LINE_THRESHOLD = 1500;
var DIFF_AUTO_COLLAPSE_SINGLE_FILE_LINE_THRESHOLD = 800;
var DIFF_FILE_LINE_CHUNK = 400;

function _diffRendersInModal() {
  return true;
}

function _diffShellOpen() {
  return _diffRendersInModal()
    ? '<div class="modal ui-modal ui-modal--xl ui-modal--viewport ui-modal--structured diff-view-modal diff-view" role="dialog" aria-modal="true" aria-labelledby="diff-view-title">'
    : '<div class="diff-view">';
}

function _diffShellClose() {
  return '</div>';
}

function showDiffView(agentId, readOnly) {
  if (!agentId) return;
  _diffViewOpen = true;
  _diffViewAgentId = agentId;
  _diffViewData = null;
  _diffCollapsedFiles = {};
  _diffCollapseAllFiles = false;
  _diffCollapseInitialized = false;
  _diffRenderedLineLimits = {};
  _diffMergeCheck = null;
  _diffCommitMsg = '';
  _diffMerging = false;
  _diffMergeProgress = null;
  _diffReadOnly = !!readOnly;
  renderDiffView();
  send({ cmd: 'worktree_diff_full', id: agentId });
  if (!_diffReadOnly) send({ cmd: 'worktree_check_merge', id: agentId });
}

function hideDiffView() {
  _diffViewOpen = false;
  _diffViewAgentId = '';
  _diffViewData = null;
  _diffCollapsedFiles = {};
  _diffCollapseAllFiles = false;
  _diffCollapseInitialized = false;
  _diffRenderedLineLimits = {};
  _diffMergeCheck = null;
  _diffCommitMsg = '';
  _diffMerging = false;
  _diffMergeProgress = null;
  _diffReadOnly = false;
  renderDiffView();
}

function diffReceiveFull(msg) {
  if (!_diffViewOpen || !_diffViewAgentId || msg.id !== _diffViewAgentId) return;
  _diffViewData = msg;
  _initializeDiffProgressiveDisclosure(msg.files || []);
  renderDiffView();
}

function diffReceiveMergeCheck(msg) {
  if (!_diffViewOpen || msg.id !== _diffViewAgentId) return;
  _diffMergeCheck = msg;
  if (msg.clean && !_diffCommitMsg) {
    if (msg.default_message) {
      _diffCommitMsg = msg.default_message;
    } else {
      var cell = state.agents[_diffViewAgentId];
      if (cell) {
        var squash = cell.worktree_merge_squash !== false;
        _diffCommitMsg = squash
          ? 'Squash merge: ' + (cell.worktree_branch || cell.name)
          : "Merge branch '" + (cell.worktree_branch || cell.name) + "'";
      }
    }
  }
  renderDiffView();
}

function diffReceiveMergeResult(msg) {
  _diffMerging = false;
  _diffMergeProgress = msg && msg.ok
    ? { phase: 'done', message: msg.message || 'Done' }
    : null;
  if (msg.ok) {
    hideDiffView();
  } else {
    _diffMergeCheck = { clean: false, error: msg.error || 'Merge failed' };
    renderDiffView();
  }
}

function diffReceiveMergeProgress(msg) {
  if (!_diffViewOpen || !_diffViewAgentId || msg.id !== _diffViewAgentId) return;
  _diffMerging = true;
  _diffMergeProgress = {
    phase: msg.phase || '',
    message: msg.message || '',
  };
  renderDiffView();
}

function diffReceiveRebaseResult(msg) {
  if (msg.ok) {
    // Re-check merge status after rebase
    _diffMergeCheck = null;
    send({ cmd: 'worktree_check_merge', id: _diffViewAgentId });
    // Also refresh the diff
    _diffViewData = null;
    send({ cmd: 'worktree_diff_full', id: _diffViewAgentId });
  } else {
    _diffMergeCheck = { clean: false, error: msg.error || 'Rebase failed' };
  }
  renderDiffView();
}

function toggleDiffFile(path) {
  if (!path) return;
  _setDiffFileCollapsed(path, !_isDiffFileCollapsed(path));
  renderDiffView();
}

function _isDiffFileCollapsed(path) {
  if (!path) return false;
  if (_diffCollapsedFiles[path] === 'collapsed') return true;
  if (_diffCollapsedFiles[path] === 'expanded') return false;
  return !!_diffCollapseAllFiles;
}

function _setDiffFileCollapsed(path, collapsed) {
  if (!path) return;
  if (!!collapsed === !!_diffCollapseAllFiles) {
    delete _diffCollapsedFiles[path];
    return;
  }
  _diffCollapsedFiles[path] = collapsed ? 'collapsed' : 'expanded';
}

function _diffFileLineCount(file) {
  var count = 0;
  var hunks = (file && file.hunks) || [];
  for (var i = 0; i < hunks.length; i++) {
    count += ((hunks[i] && hunks[i].lines) || []).length;
  }
  return count;
}

function _diffShouldAutoCollapse(files) {
  files = files || [];
  if (files.length > DIFF_AUTO_COLLAPSE_FILE_THRESHOLD) return true;
  var totalLines = 0;
  for (var i = 0; i < files.length; i++) {
    var fileLines = _diffFileLineCount(files[i]);
    if (files.length === 1
        && fileLines > DIFF_AUTO_COLLAPSE_SINGLE_FILE_LINE_THRESHOLD) {
      return true;
    }
    totalLines += fileLines;
    if (totalLines > DIFF_AUTO_COLLAPSE_LINE_THRESHOLD) return true;
  }
  return false;
}

function _initializeDiffProgressiveDisclosure(files) {
  if (_diffCollapseInitialized) return;
  _diffCollapseInitialized = true;
  files = files || [];
  if (!_diffShouldAutoCollapse(files)) return;
  _diffCollapseAllFiles = true;
  for (var i = 0; i < files.length; i++) {
    var file = files[i] || {};
    var path = file.path || '';
    if (path && _diffFileLineCount(file) <= DIFF_FILE_LINE_CHUNK) {
      _diffCollapsedFiles[path] = 'expanded';
      break;
    }
  }
}

function diffShowMoreLines(path) {
  if (!path) return;
  var current = Number(_diffRenderedLineLimits[path] || DIFF_FILE_LINE_CHUNK);
  _diffRenderedLineLimits[path] = current + DIFF_FILE_LINE_CHUNK;
  renderDiffView();
}

function diffSetAllFilesCollapsed(collapsed) {
  _diffCollapseAllFiles = !!collapsed;
  _diffCollapsedFiles = {};
  renderDiffView();
}

function _syncDiffCollapsedFiles(files) {
  var active = {};
  for (var i = 0; i < files.length; i++) {
    var path = files[i] && files[i].path;
    if (path) active[path] = true;
  }
  for (var key in _diffCollapsedFiles) {
    if (!active[key]) delete _diffCollapsedFiles[key];
  }
  for (var limitPath in _diffRenderedLineLimits) {
    if (!active[limitPath]) delete _diffRenderedLineLimits[limitPath];
  }
}

function _diffCollapsedCount(files) {
  var count = 0;
  for (var i = 0; i < files.length; i++) {
    if (_isDiffFileCollapsed(files[i] && files[i].path)) count++;
  }
  return count;
}

function _diffStatsLabel(stats) {
  if (!stats) return '0 files changed';
  return (stats.files || 0) + ' files changed, +'
    + (stats.insertions || 0) + ' -' + (stats.deletions || 0);
}

function _diffStatusLabel(status) {
  var labels = {
    modified: 'Modified',
    added: 'Added',
    deleted: 'Deleted',
    renamed: 'Renamed',
    copied: 'Copied',
  };
  return labels[status] || 'Changed';
}

function _diffSyntheticArtifact() {
  if (!_diffViewData || !_diffViewData.stats) return null;
  var files = _diffViewData.files || [];
  var previewLines = [];
  for (var i = 0; i < files.length && i < 8; i++) {
    var file = files[i];
    previewLines.push((_diffStatusLabel(file.status) + ': ' + (file.path || '(unknown file)')).trim());
  }
  return _artifactNormalizeClient({
    id: 'review-diff',
    type: 'diff',
    title: 'Worktree diff',
    summary: _diffStatsLabel(_diffViewData.stats),
    content: previewLines.join('\n'),
    metadata: {
      files: _diffViewData.stats.files || files.length,
      insertions: _diffViewData.stats.insertions || 0,
      deletions: _diffViewData.stats.deletions || 0,
    },
    prompt: { mode: 'summary' },
    storage: { kind: 'inline', path: '', content: previewLines.join('\n') },
  }, 0);
}

function _diffRelatedArtifacts() {
  if (!state || !state.board_tasks) return [];
  var agent = state.agents ? state.agents[_diffViewAgentId] : null;
  var currentTaskId = agent ? (agent.current_task_id || '') : '';
  var tasks = [];
  for (var id in state.board_tasks) {
    var task = state.board_tasks[id];
    if (task.agent_id === _diffViewAgentId) tasks.push(task);
  }
  tasks.sort(function(a, b) {
    if (a.id === currentTaskId) return -1;
    if (b.id === currentTaskId) return 1;
    if (a.lane !== 'Done' && b.lane === 'Done') return -1;
    if (a.lane === 'Done' && b.lane !== 'Done') return 1;
    return (b.updated_at || '').localeCompare(a.updated_at || '');
  });
  var artifacts = [];
  var synthetic = _diffSyntheticArtifact();
  if (synthetic) artifacts.push(synthetic);
  for (var i = 0; i < tasks.length; i++) {
    var combined = typeof _taskArtifactsCombined === 'function'
      ? _taskArtifactsCombined(tasks[i])
      : [];
    for (var j = 0; j < combined.length; j++) {
      combined[j].taskId = combined[j].taskId || tasks[i].id;
      combined[j].taskLabel = combined[j].taskLabel || tasks[i].task || tasks[i].id;
      artifacts.push(combined[j]);
      if (artifacts.length >= 12) return artifacts;
    }
  }
  return artifacts;
}

function _renderDiffArtifacts() {
  var artifacts = _diffRelatedArtifacts();
  if (!artifacts.length || typeof _renderArtifactCollection !== 'function') return '';
  var html = '<section class="diff-artifacts-section">';
  html += '<div class="diff-artifacts-header">';
  html += '<div class="diff-artifacts-title">Review artifacts</div>';
  html += '<div class="diff-artifacts-subtitle">Logs, reports, images, and the synthesized diff summary for this branch.</div>';
  html += '</div>';
  html += _renderArtifactCollection(artifacts, {
    empty: 'No review artifacts available.',
    cardOptions: { showTaskLabel: true },
  });
  html += '</section>';
  return html;
}

function _boundaryShortSha(boundary) {
  if (!boundary || !boundary.commit_sha) return '';
  return String(boundary.commit_sha).slice(0, 8);
}

function _renderBoundarySummary() {
  var boundary = (_diffMergeCheck && _diffMergeCheck.boundary)
    || (_diffViewData && _diffViewData.boundary);
  if (!boundary || !boundary.boundary) return '';
  var meta = boundary.boundary || {};
  var clean = !!((_diffMergeCheck && _diffMergeCheck.clean_boundary)
    || (_diffViewData && _diffViewData.clean_boundary));
  var html = '<section class="diff-boundary-section">';
  html += '<div class="diff-boundary-title">Latest task boundary</div>';
  html += '<div class="diff-boundary-row">';
  html += '<span class="diff-boundary-task">' + esc(boundary.task_title || boundary.task_id || 'Task boundary') + '</span>';
  html += '<span class="diff-boundary-badge ' + (clean ? 'clean' : 'blocked') + '">'
    + esc(clean ? 'clean mergeable' : 'blocked') + '</span>';
  html += '</div>';
  html += '<div class="diff-boundary-meta">';
  if (meta.kind) html += '<span>' + esc(meta.kind) + '</span>';
  if (meta.status) html += '<span>' + esc(meta.status) + '</span>';
  if (meta.commit_sha) html += '<span>' + esc(_boundaryShortSha(meta)) + '</span>';
  html += '</div>';
  if (boundary.queued_followers && boundary.queued_followers.length) {
    html += '<div class="diff-boundary-followers">Queued after this boundary: '
      + esc(boundary.queued_followers.map(function(f) { return f.task_title; }).join(', '))
      + '</div>';
  }
  if (boundary.started_followers && boundary.started_followers.length) {
    html += '<div class="diff-boundary-followers blocked">Started after this boundary: '
      + esc(boundary.started_followers.map(function(f) { return f.task_title; }).join(', '))
      + '</div>';
  }
  html += '</section>';
  return html;
}

function _diffStaleBaseWarning() {
  return (_diffMergeCheck && _diffMergeCheck.stale_base_warning)
    || (_diffViewData && _diffViewData.stale_base_warning)
    || '';
}

function _renderStaleBaseWarning() {
  var warning = _diffStaleBaseWarning();
  if (!warning) return '';
  return '<section class="diff-stale-base-warning"><pre>'
    + esc(warning) + '</pre></section>';
}

function _renderDiffLines(lines) {
  var html = '';
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    var cls = 'diff-line-context';
    var prefix = ' ';
    if (line.type === 'add') {
      cls = 'diff-line-add';
      prefix = '+';
    } else if (line.type === 'del') {
      cls = 'diff-line-del';
      prefix = '-';
    }
    html += '<div class="diff-line ' + cls + '"><span class="diff-line-prefix">'
      + esc(prefix) + '</span><span class="diff-line-text">' + esc(line.text || '')
      + '</span></div>';
  }
  return html;
}

function _renderDiffFile(file) {
  var path = file.path || '(unknown file)';
  var collapsed = _isDiffFileCollapsed(path);
  var status = _diffStatusLabel(file.status);
  var summary = '';
  if (file.insertions || file.deletions) {
    summary = '<span class="diff-file-stat diff-file-add">+'
      + (file.insertions || 0) + '</span><span class="diff-file-stat diff-file-del">-'
      + (file.deletions || 0) + '</span>';
  }
  var html = '<section class="diff-file-section">';
  html += '<button class="diff-file-header" data-path="' + esc(path)
    + '" onclick="toggleDiffFile(this.dataset.path)">';
  html += '<span class="diff-file-arrow">' + (collapsed ? '&#9654;' : '&#9660;') + '</span>';
  html += '<span class="diff-file-path">' + esc(path) + '</span>';
  html += '<span class="diff-file-status">' + esc(status) + '</span>';
  if (summary) html += '<span class="diff-file-summary">' + summary + '</span>';
  html += '</button>';
  if (!collapsed) {
    html += '<div class="diff-file-body">';
    if (file.binary) {
      html += '<div class="diff-binary">Binary file changed</div>';
    } else if (!file.hunks || !file.hunks.length) {
      html += '<div class="diff-binary">No line-by-line diff for this file</div>';
    } else {
      var totalLines = _diffFileLineCount(file);
      var lineLimit = Number(
        _diffRenderedLineLimits[path] || DIFF_FILE_LINE_CHUNK
      );
      var renderedLines = 0;
      for (var i = 0; i < file.hunks.length; i++) {
        if (renderedLines >= lineLimit) break;
        var hunk = file.hunks[i];
        var hunkLines = hunk.lines || [];
        var visibleLines = hunkLines.slice(
          0,
          Math.max(0, lineLimit - renderedLines)
        );
        html += '<div class="diff-hunk">';
        html += '<div class="diff-hunk-header">' + esc(hunk.header || '') + '</div>';
        html += '<div class="diff-hunk-body">' + _renderDiffLines(visibleLines) + '</div>';
        html += '</div>';
        renderedLines += visibleLines.length;
        if (visibleLines.length < hunkLines.length) break;
      }
      if (renderedLines < totalLines) {
        html += '<button class="diff-file-load-more diff-collapse-btn" data-path="'
          + esc(path) + '" onclick="diffShowMoreLines(this.dataset.path)">Show '
          + Math.min(DIFF_FILE_LINE_CHUNK, totalLines - renderedLines)
          + ' more lines <span class="diff-collapse-summary">'
          + (totalLines - renderedLines) + ' remaining</span></button>';
      }
    }
    html += '</div>';
  }
  html += '</section>';
  return html;
}

function _renderDiffCollapseControls(files) {
  if (!files.length) return '';
  var collapsedCount = _diffCollapsedCount(files);
  var html = '<div class="diff-view-toolbar ui-toolbar ui-toolbar--bordered">';
  html += '<button class="diff-collapse-btn"'
    + (_diffCollapseAllFiles ? ' disabled' : '')
    + ' onclick="diffSetAllFilesCollapsed(true)">Collapse all</button>';
  html += '<button class="diff-collapse-btn"'
    + (!_diffCollapseAllFiles && !collapsedCount ? ' disabled' : '')
    + ' onclick="diffSetAllFilesCollapsed(false)">Expand all</button>';
  html += '<span class="diff-collapse-summary">' + collapsedCount + ' of '
    + files.length + ' collapsed</span>';
  html += '</div>';
  return html;
}

async function proceedDiffMerge() {
  if (!_diffViewAgentId) return;
  var ok = await _confirmWorktreeMerge(_diffViewAgentId, _diffCommitMsg);
  if (ok) {
    _diffMerging = true;
    _diffMergeProgress = {
      phase: 'request',
      message: 'Starting Create PR + Merge\u2026',
    };
    renderDiffView();
  }
}

function _diffMergeProgressLabel() {
  if (_diffMergeProgress && _diffMergeProgress.message) {
    return _diffMergeProgress.message;
  }
  var phase = _diffMergeProgress ? String(_diffMergeProgress.phase || '') : '';
  if (phase === 'preflight') return 'Checking merge readiness\u2026';
  if (phase === 'push_branch') return 'Pushing branch\u2026';
  if (phase === 'pr_create') return 'Creating PR\u2026';
  if (phase === 'pr_merge') return 'Merging PR\u2026';
  if (phase === 'finalize') return 'Finalizing merge\u2026';
  if (phase === 'direct_merge') return 'Merging locally\u2026';
  if (phase === 'done') return 'Done';
  return 'Creating PR and merging\u2026';
}

function _renderMergeBanner() {
  if (!_diffMergeCheck) {
    return '<div class="diff-merge-banner loading">Checking merge compatibility\u2026</div>';
  }
  if (_diffMergeCheck.error) {
    return '<div class="diff-merge-banner conflict">' + esc(_diffMergeCheck.error) + '</div>';
  }
  if (_diffMergeCheck.dirty) {
    return '<div class="diff-merge-banner dirty">Uncommitted changes \u2014 checkpoint before merging</div>';
  }
  if (_diffMergeCheck.stale_base_warning) {
    return '<div class="diff-merge-banner stale">'
      + esc(_diffMergeCheck.stale_base_warning) + '</div>';
  }
  if (!_diffMergeCheck.clean) {
    var n = (_diffMergeCheck.conflicts || []).length;
    var label = n > 0
      ? n + ' conflict' + (n !== 1 ? 's' : '') + ' \u2014 merge blocked'
      : 'Merge blocked \u2014 branches have diverged';
    var html = '<div class="diff-merge-banner conflict">' + esc(label) + '</div>';
    if (n > 0) {
      html += '<div class="diff-conflict-list">';
      for (var i = 0; i < _diffMergeCheck.conflicts.length; i++) {
        var c = _diffMergeCheck.conflicts[i];
        html += '<div class="diff-conflict-file">' + esc(c.path || '')
          + ' <span class="diff-conflict-reason">' + esc(c.reason || '') + '</span></div>';
      }
      html += '</div>';
    }
    return html;
  }
  return '<div class="diff-merge-banner clean">Clean merge \u2014 no conflicts</div>';
}

function _renderDiffFooter() {
  var html = '<div class="diff-footer ui-modal__footer">';

  // Commit message textarea (only when merge is clean)
  if (_diffMergeCheck && _diffMergeCheck.clean) {
    html += '<textarea class="diff-commit-msg" placeholder="Commit message\u2026"'
      + ' oninput="_diffCommitMsg=this.value">' + esc(_diffCommitMsg) + '</textarea>';
  }

  html += '<div class="diff-footer-buttons">';
  if (_diffMerging) {
    html += '<span class="diff-merge-progress" role="status" aria-live="polite">'
      + '<span class="diff-spinner" aria-hidden="true"></span>'
      + '<span>' + esc(_diffMergeProgressLabel()) + '</span>'
      + '</span>';
  }
  html += '<button class="btn-cancel" onclick="hideDiffView()">Cancel</button>';

  if (_diffMergeCheck && _diffMergeCheck.dirty) {
    html += '<button class="btn-success" onclick="_diffCheckpointAndRecheck()">Checkpoint</button>';
  } else if (_diffMergeCheck && _diffMergeCheck.stale_base) {
    html += '<button class="btn-warning" onclick="_diffRebase()">Rebase onto Main</button>';
  } else if (_diffMergeCheck && !_diffMergeCheck.clean && !_diffMergeCheck.error) {
    html += '<button class="btn-warning" onclick="_diffRebase()">Rebase onto Main</button>';
  }

  var canMerge = _diffMergeCheck && _diffMergeCheck.clean && !_diffMerging;
  var mergeLabel = _diffMerging ? _diffMergeProgressLabel() : 'Create PR & Merge';
  html += '<button class="btn-success diff-merge-button"' + (canMerge ? '' : ' disabled')
    + ' onclick="proceedDiffMerge()">'
    + (_diffMerging ? '<span class="diff-spinner diff-spinner-inline" aria-hidden="true"></span>' : '')
    + '<span>' + esc(mergeLabel) + '</span></button>';
  html += '</div></div>';
  return html;
}

function _diffCheckpointAndRecheck() {
  send({ cmd: 'worktree_checkpoint', id: _diffViewAgentId });
  _diffMergeCheck = null;
  // Re-check after a short delay to let checkpoint complete
  setTimeout(function() {
    send({ cmd: 'worktree_check_merge', id: _diffViewAgentId });
  }, 1500);
  renderDiffView();
}

function _diffRebase() {
  send({ cmd: 'worktree_rebase', id: _diffViewAgentId });
  _diffMergeCheck = null;
  renderDiffView();
}

function renderDiffView() {
  var root = document.getElementById('diff-view-root');
  if (!root) return;

  var modal = _diffRendersInModal();
  document.body.classList.toggle('diff-view-open', _diffViewOpen && !modal);
  if (typeof setWorktreeDiffModalVisible === 'function') {
    setWorktreeDiffModalVisible(_diffViewOpen && modal);
  } else if (root.classList) {
    root.classList.remove('overlay');
    root.classList.remove('visible');
    root.classList.remove('modal-nested');
    root.onclick = null;
  }
  if (!_diffViewOpen) {
    root.innerHTML = '';
    return;
  }

  var html = _diffShellOpen();
  if (!_diffViewData) {
    html += '<div class="diff-view-header ui-modal__header ui-modal__header--bordered">';
    html += '<div id="diff-view-title" class="diff-view-title ui-modal__title">Loading diff\u2026</div>';
    html += '</div>';
    html += '<div class="diff-view-content ui-modal__body ui-modal__body--flush"><div class="diff-empty ui-state ui-state--loading ui-state--fill" role="status" aria-live="polite">Loading worktree diff\u2026</div></div>';
    html += _renderMergeBanner();
    html += '<div class="diff-footer ui-modal__footer">';
    html += '<button class="btn-cancel" onclick="hideDiffView()">Cancel</button>';
    html += '</div>';
    html += _diffShellClose();
    root.innerHTML = html;
    return;
  }

  if (_diffViewData.error) {
    html += '<div class="diff-view-header ui-modal__header ui-modal__header--bordered">';
    html += '<div id="diff-view-title" class="diff-view-title ui-modal__title">Unable to load diff</div>';
    html += '</div>';
    html += '<div class="diff-view-content ui-modal__body ui-modal__body--flush"><div class="diff-empty ui-state ui-state--error ui-state--fill" role="alert">' + esc(_diffViewData.error) + ' Close the viewer and try again.</div></div>';
    html += '<div class="diff-footer ui-modal__footer">';
    html += '<button class="btn-cancel" onclick="hideDiffView()">Cancel</button>';
    html += '</div>';
    html += _diffShellClose();
    root.innerHTML = html;
    return;
  }

  var stats = _diffViewData.stats || {};
  var files = _diffViewData.files || [];
  _syncDiffCollapsedFiles(files);
  html += '<div class="diff-view-header ui-modal__header ui-modal__header--bordered">';
  html += '<div id="diff-view-title" class="diff-view-title ui-modal__title">' + esc(_diffViewData.agent_name || 'Worktree diff') + '</div>';
  html += '<div class="diff-view-branch">' + esc(_diffViewData.branch || '') + ' \u2192 '
    + esc(_diffViewData.base_branch || 'main') + '</div>';
  html += '<div class="diff-view-stats">' + esc(_diffStatsLabel(stats)) + '</div>';
  html += _renderDiffCollapseControls(files);
  html += '</div>';
  html += '<div class="diff-view-content ui-modal__body ui-modal__body--flush">';
  html += _renderStaleBaseWarning();
  html += _renderBoundarySummary();
  html += _renderDiffArtifacts();
  if (!files.length) {
    html += '<div class="diff-empty ui-state ui-state--empty ui-state--fill">No changes to review.</div>';
  } else {
    for (var i = 0; i < files.length; i++) {
      html += _renderDiffFile(files[i]);
    }
  }
  html += '</div>';
  if (_diffReadOnly) {
    html += '<div class="diff-footer ui-modal__footer"><div class="diff-footer-buttons">';
    html += '<button class="btn-cancel" onclick="hideDiffView()">Close</button>';
    html += '</div></div>';
  } else {
    html += _renderMergeBanner();
    html += _renderDiffFooter();
  }
  html += _diffShellClose();

  root.innerHTML = html;
}

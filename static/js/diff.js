/* Full-panel worktree diff review view */

var _diffViewOpen = false;
var _diffViewAgentId = '';
var _diffViewData = null;
var _diffCollapsedFiles = {};
var _diffMergeCheck = null;   // null = loading, {clean, dirty, conflicts}
var _diffCommitMsg = '';       // editable commit message
var _diffMerging = false;      // true while merge request in flight

function showDiffView(agentId) {
  if (!agentId) return;
  _diffViewOpen = true;
  _diffViewAgentId = agentId;
  _diffViewData = null;
  _diffCollapsedFiles = {};
  _diffMergeCheck = null;
  _diffCommitMsg = '';
  _diffMerging = false;
  renderDiffView();
  send({ cmd: 'worktree_diff_full', id: agentId });
  send({ cmd: 'worktree_check_merge', id: agentId });
}

function hideDiffView() {
  _diffViewOpen = false;
  _diffViewAgentId = '';
  _diffViewData = null;
  _diffCollapsedFiles = {};
  _diffMergeCheck = null;
  _diffCommitMsg = '';
  _diffMerging = false;
  renderDiffView();
}

function diffReceiveFull(msg) {
  if (!_diffViewOpen || !_diffViewAgentId || msg.id !== _diffViewAgentId) return;
  _diffViewData = msg;
  renderDiffView();
}

function diffReceiveMergeCheck(msg) {
  if (!_diffViewOpen || msg.id !== _diffViewAgentId) return;
  _diffMergeCheck = msg;
  if (msg.clean && !_diffCommitMsg) {
    var cell = state.agents[_diffViewAgentId];
    if (cell) {
      var squash = cell.worktree_merge_squash !== false;
      _diffCommitMsg = squash
        ? 'Squash merge: ' + (cell.worktree_branch || cell.name)
        : "Merge branch '" + (cell.worktree_branch || cell.name) + "'";
    }
  }
  renderDiffView();
}

function diffReceiveMergeResult(msg) {
  _diffMerging = false;
  if (msg.ok) {
    hideDiffView();
  } else {
    _diffMergeCheck = { clean: false, error: msg.error || 'Merge failed' };
    renderDiffView();
  }
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
  _diffCollapsedFiles[path] = !_diffCollapsedFiles[path];
  renderDiffView();
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
  var collapsed = !!_diffCollapsedFiles[path];
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
      for (var i = 0; i < file.hunks.length; i++) {
        var hunk = file.hunks[i];
        html += '<div class="diff-hunk">';
        html += '<div class="diff-hunk-header">' + esc(hunk.header || '') + '</div>';
        html += '<div class="diff-hunk-body">' + _renderDiffLines(hunk.lines || []) + '</div>';
        html += '</div>';
      }
    }
    html += '</div>';
  }
  html += '</section>';
  return html;
}

async function proceedDiffMerge() {
  if (!_diffViewAgentId) return;
  var ok = await _confirmWorktreeMerge(_diffViewAgentId, _diffCommitMsg);
  if (ok) {
    _diffMerging = true;
    renderDiffView();
  }
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
  if (!_diffMergeCheck.clean) {
    var n = (_diffMergeCheck.conflicts || []).length;
    var html = '<div class="diff-merge-banner conflict">'
      + n + ' conflict' + (n !== 1 ? 's' : '') + ' \u2014 merge blocked</div>';
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
  var html = '<div class="diff-footer">';

  // Commit message textarea (when merge check loaded and not dirty)
  if (_diffMergeCheck && !_diffMergeCheck.dirty) {
    html += '<textarea class="diff-commit-msg" placeholder="Commit message\u2026"'
      + ' oninput="_diffCommitMsg=this.value">' + esc(_diffCommitMsg) + '</textarea>';
  }

  html += '<div class="diff-footer-buttons">';
  html += '<button class="btn-cancel" onclick="hideDiffView()">Cancel</button>';

  if (_diffMergeCheck && _diffMergeCheck.dirty) {
    html += '<button class="btn-green" onclick="_diffCheckpointAndRecheck()">Checkpoint</button>';
  } else if (_diffMergeCheck && !_diffMergeCheck.clean && !_diffMergeCheck.error) {
    html += '<button class="btn-rebase" onclick="_diffRebase()">Rebase onto Main</button>';
  }

  var canMerge = _diffMergeCheck && _diffMergeCheck.clean && !_diffMerging;
  html += '<button class="btn-green"' + (canMerge ? '' : ' disabled')
    + ' onclick="proceedDiffMerge()">'
    + (_diffMerging ? 'Merging\u2026' : 'Proceed to Merge') + '</button>';
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

  document.body.classList.toggle('diff-view-open', _diffViewOpen);
  if (!_diffViewOpen) {
    root.innerHTML = '';
    return;
  }

  var html = '<div class="diff-view">';
  if (!_diffViewData) {
    html += '<div class="diff-view-header">';
    html += '<div class="diff-view-title">Loading diff\u2026</div>';
    html += '</div>';
    html += '<div class="diff-view-content"><div class="diff-empty">Loading worktree diff\u2026</div></div>';
    html += _renderMergeBanner();
    html += '<div class="diff-footer">';
    html += '<button class="btn-cancel" onclick="hideDiffView()">Cancel</button>';
    html += '</div>';
    html += '</div>';
    root.innerHTML = html;
    return;
  }

  if (_diffViewData.error) {
    html += '<div class="diff-view-header">';
    html += '<div class="diff-view-title">Unable to load diff</div>';
    html += '</div>';
    html += '<div class="diff-view-content"><div class="diff-empty">' + esc(_diffViewData.error) + '</div></div>';
    html += '<div class="diff-footer">';
    html += '<button class="btn-cancel" onclick="hideDiffView()">Cancel</button>';
    html += '</div>';
    html += '</div>';
    root.innerHTML = html;
    return;
  }

  var stats = _diffViewData.stats || {};
  var files = _diffViewData.files || [];
  html += '<div class="diff-view-header">';
  html += '<div class="diff-view-title">' + esc(_diffViewData.agent_name || 'Worktree diff') + '</div>';
  html += '<div class="diff-view-branch">' + esc(_diffViewData.branch || '') + ' \u2192 '
    + esc(_diffViewData.base_branch || 'main') + '</div>';
  html += '<div class="diff-view-stats">' + esc(_diffStatsLabel(stats)) + '</div>';
  html += '</div>';
  html += '<div class="diff-view-content">';
  if (!files.length) {
    html += '<div class="diff-empty">No changes to review.</div>';
  } else {
    for (var i = 0; i < files.length; i++) {
      html += _renderDiffFile(files[i]);
    }
  }
  html += '</div>';
  html += _renderMergeBanner();
  html += _renderDiffFooter();
  html += '</div>';

  root.innerHTML = html;
}

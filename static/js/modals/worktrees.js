/* Worktree and history modals. */

function _toggleWorktreeFields() {
  const on = document.getElementById('gs-worktree').checked;
  document.getElementById('gs-wt-fields').style.display = on ? 'block' : 'none';
}

/* -- Worktree symlinks list ------------------------------------------------ */
let _gsWtSymlinks = [];

function _renderWtSymlinks() {
  const container = document.getElementById('gs-wt-symlinks-list');
  container.innerHTML = '';
  for (let i = 0; i < _gsWtSymlinks.length; i++) {
    const chip = document.createElement('span');
    chip.className = 'wt-symlink-chip';
    chip.textContent = _gsWtSymlinks[i];
    const btn = document.createElement('button');
    btn.textContent = '×';
    btn.onclick = () => { _gsWtSymlinks.splice(i, 1); _renderWtSymlinks(); };
    chip.appendChild(btn);
    container.appendChild(chip);
  }
}

function _addWtSymlink() {
  const input = document.getElementById('gs-wt-symlink-input');
  const val = input.value.trim().replace(/^\/+|\/+$/g, '');
  if (!val || _gsWtSymlinks.includes(val)) return;
  _gsWtSymlinks.push(val);
  _renderWtSymlinks();
  input.value = '';
}

function _addWtSymlinkPreset(path) {
  if (!_gsWtSymlinks.includes(path)) {
    _gsWtSymlinks.push(path);
    _renderWtSymlinks();
  }
}

function _toggleAddWorktreeFields() {
  const on = document.getElementById('add-wt-enabled').checked;
  document.getElementById('add-wt-fields').style.display = on ? 'block' : 'none';
}

/* -- Worktree History ----------------------------------------------------- */
let _histCellId = null;

function _showWorktreeHistory(data) {
  _histCellId = data.id;
  const cell = state.agents[data.id];
  const name = cell ? cell.name : data.id;
  const branch = (data.branch || '').replace(/^torque\//, '');

  document.getElementById('hist-title').textContent = name + ' History';
  document.getElementById('hist-branch').textContent = branch ? '\u2387 ' + branch : '';

  const list = document.getElementById('hist-list');
  if (!data.commits || data.commits.length === 0) {
    list.innerHTML = '<div class="hist-empty">No commits on this branch yet.</div>';
  } else {
    let html = '';
    for (let i = 0; i < data.commits.length; i++) {
      const c = data.commits[i];
      const isCurrent = i === 0;
      const dateStr = _formatHistDate(c.date);
      const hasBody = !!(c.body && c.body.trim());
      const clickable = hasBody ? ' hist-clickable' : '';
      const toggle = hasBody ? ` onclick="_toggleHistBody(this)"` : '';
      html += `<div class="hist-row${isCurrent ? ' hist-current' : ''}${clickable}"${toggle}>`;
      html += `  <div class="hist-dot"></div>`;
      html += `  <div class="hist-info">`;
      html += `    <div class="hist-msg">${esc(c.message)}</div>`;
      let statStr = '';
      if (c.insertions || c.deletions) statStr = ` \u00b7 +${c.insertions || 0} -${c.deletions || 0}`;
      html += `    <div class="hist-meta">${esc(c.short_sha)} \u00b7 ${esc(dateStr)}${statStr}</div>`;
      if (hasBody) {
        html += `    <div class="hist-body">${esc(c.body)}</div>`;
      }
      html += `  </div>`;
      if (!isCurrent) {
        html += `  <button class="hist-rollback" onclick="event.stopPropagation();_doRollback('${esc(c.sha)}')" title="Roll back to this commit">\u21BA</button>`;
      } else {
        html += `  <span class="hist-tag">HEAD</span>`;
      }
      html += `</div>`;
    }
    list.innerHTML = html;
  }

  document.getElementById('modal-history').classList.add('visible');
}

function _formatHistDate(iso) {
  try {
    const d = new Date(iso);
    const now = new Date();
    const diff = now - d;
    if (diff < 60000) return 'just now';
    if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
    if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch (_) { return iso; }
}

function _toggleHistBody(row) {
  row.classList.toggle('hist-expanded');
}

async function _doRollback(sha) {
  if (!_histCellId) return;
  const cell = state.agents[_histCellId];
  const name = cell ? cell.name : _histCellId;
  if (await showConfirm(`Roll back "${name}" to ${sha.slice(0, 7)}? Changes after this commit will be lost.`)) {
    send({ cmd: 'worktree_rollback', id: _histCellId, sha });
    closeModals();
  }
}

/* -- Action/task modal modules extracted to static/js/modals/*.js ------- */

function _showPromptPreview(msg) {
  document.getElementById('prompt-preview-content').textContent = msg.prompt || '(empty)';
  var warnEl = document.getElementById('prompt-preview-warning');
  if (msg.warning) {
    warnEl.textContent = msg.warning;
    warnEl.style.display = '';
  } else {
    warnEl.style.display = 'none';
  }
  document.getElementById('modal-prompt-preview').classList.add('visible');
}

function copyPromptPreview() {
  var text = document.getElementById('prompt-preview-content').textContent;
  navigator.clipboard.writeText(text).then(function() {
    var btn = document.getElementById('prompt-preview-copy-btn');
    btn.textContent = 'Copied!';
    setTimeout(function() { btn.textContent = 'Copy'; }, 1500);
  });
}

/* -- Global Settings ---------------------------------------------------- */

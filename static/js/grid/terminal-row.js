var _dismissedPendingHireIds = {};

function _activePendingHireItems() {
  if (!state || !state.pending_hires) return [];
  return Object.values(state.pending_hires).filter(function(hire) {
    return String((hire && hire.status) || 'pending') === 'pending';
  }).sort(function(a, b) {
    const aTs = Number((a && (a.created_at || a.updated_at)) || 0);
    const bTs = Number((b && (b.created_at || b.updated_at)) || 0);
    if (aTs !== bTs) return aTs - bTs;
    return String((a && a.id) || '').localeCompare(String((b && b.id) || ''));
  });
}

function _pruneDismissedPendingHireIds() {
  const liveIds = new Set(_activePendingHireItems().map(function(hire) {
    return String((hire && hire.id) || '');
  }));
  Object.keys(_dismissedPendingHireIds || {}).forEach(function(id) {
    if (!liveIds.has(id)) delete _dismissedPendingHireIds[id];
  });
}

function _pendingHireBannerItem() {
  _pruneDismissedPendingHireIds();
  const hires = _activePendingHireItems().filter(function(hire) {
    return !_dismissedPendingHireIds[String((hire && hire.id) || '')];
  });
  return {
    current: hires.length ? hires[0] : null,
    remaining: Math.max(0, hires.length - 1),
  };
}

function _dismissPendingHire(id) {
  const key = String(id || '').trim();
  if (!key) return;
  _dismissedPendingHireIds[key] = Date.now();
}

function renderPendingHireBanner() {
  const el = document.getElementById('pending-hire-banner');
  if (!el) return;
  const banner = _pendingHireBannerItem();
  const hire = banner.current;
  if (!hire) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  const architect = state && state.agents ? state.agents[hire.architect_id] : null;
  const architectName = architect ? (architect.name || architect.id) : (hire.architect_id || 'Architect');
  const hireIdJs = JSON.stringify(String(hire.id || ''));
  const moreText = banner.remaining > 0
    ? `<span class="pending-hire-banner-more">+${banner.remaining} more hire request${banner.remaining === 1 ? '' : 's'}</span>`
    : '';
  el.hidden = false;
  el.innerHTML = ''
    + `<div class="pending-hire-banner-body">`
    + `<div class="pending-hire-banner-copy">Architect <strong>${esc(architectName)}</strong> is requesting to hire a new engineer <strong>"${esc(hire.requested_name || 'Engineer')}"</strong>. ${moreText}</div>`
    + `<div class="pending-hire-banner-actions">`
    + `<button type="button" class="pending-hire-banner-btn pending-hire-banner-btn-primary" onclick='approvePendingHire(${hireIdJs})'>Approve</button>`
    + `<button type="button" class="pending-hire-banner-btn" onclick='rejectPendingHire(${hireIdJs})'>Reject with note</button>`
    + `</div></div>`;
}

function approvePendingHire(hireId) {
  const id = String(hireId || '').trim();
  if (!id || typeof send !== 'function') return;
  _dismissPendingHire(id);
  renderPendingHireBanner();
  send({ cmd: 'pending_hire_approve', id: id });
  if (typeof _showToast === 'function') {
    _showToast('Approved hire request', 'success');
  }
}

function rejectPendingHire(hireId) {
  const id = String(hireId || '').trim();
  if (!id) return;
  const hire = state && state.pending_hires ? state.pending_hires[id] : null;
  const architect = hire && state && state.agents ? state.agents[hire.architect_id] : null;
  const architectName = architect ? (architect.name || architect.id) : (hire && hire.architect_id) || 'Architect';
  const summary = hire
    ? 'Reject ' + (hire.requested_name || 'this engineer')
      + ' for ' + architectName + ' with an optional note.'
    : 'Add an optional note for the architect.';
  if (typeof openPendingHireRejectModal === 'function') {
    openPendingHireRejectModal(id, summary);
    return;
  }
  if (typeof send !== 'function') return;
  if (typeof showInputDialog === 'function') {
    return showInputDialog({
      title: 'Reject Hire Request',
      summary: summary,
      fields: [
        {
          key: 'note',
          label: 'Optional note',
          defaultValue: '',
          multiline: true,
          autofocus: true,
        },
      ],
      submitLabel: 'Reject',
      variant: 'btn-danger',
    }).then(function(values) {
      if (!values) return;
      rejectPendingHireWithNote(id, String(values.note || ''));
    });
    return;
  }
  rejectPendingHireWithNote(id, '');
}

function rejectPendingHireWithNote(hireId, note) {
  const id = String(hireId || '').trim();
  if (!id || typeof send !== 'function') return;
  _dismissPendingHire(id);
  renderPendingHireBanner();
  send({ cmd: 'pending_hire_reject', id: id, note: String(note || '') });
  if (typeof _showToast === 'function') {
    _showToast('Rejected hire request', 'success');
  }
}

function renderTermAddBtn(gname, parentId) {
  return '';
}

function renderTerminalRow(t) {
  const active = t.session_id && t.session_id === state.active_session_id;
  const cls = ['term-row', 'ui-card', 'ui-card--interactive'];
  if (active) cls.push('active', 'is-selected');
  if (t.id === focusedItemId) cls.push('focused');
  if (t.status === 'stopped') cls.push('stopped');

  const proc = t.status === 'stopped'
    ? { label: 'OFF', color: '#6e7681' }
    : processInfo(t.current_process);

  const darkCls = proc.dark ? ' dark-text' : '';
  const fullPath = t.current_path || t.directory || '';
  const pathDisplay = _formatDisplayPath(fullPath, t.git_root || t.worktree_repo_root || '');

  let h = `<div class="${cls.join(' ')}" data-nav-id="${esc(t.id)}" onclick="focusAgent('${t.id}')" oncontextmenu="onCellContextMenu(event,'${t.id}')" onauxclick="if(event.button===1){event.preventDefault();removeAgent('${t.id}')}">`;
  h += `<div class="term-badge${darkCls}" style="background:${proc.color}">${proc.label}</div>`;
  h += `<div class="term-info">`;
  h += `  <div class="term-name">${esc(t.name)}</div>`;
  if (pathDisplay) {
    h += `<div class="term-path" title="${esc(fullPath)}">${esc(pathDisplay)}</div>`;
  }
  h += `</div>`;
  h += `<div class="term-status ${t.status}"></div>`;
  h += `<div class="term-actions">`;
  h += `<button class="term-action danger" onclick="event.stopPropagation();removeAgent('${t.id}')" title="Delete" aria-label="Delete ${esc(t.name || 'terminal')}">\u2715</button>`;
  h += `</div>`;
  h += `</div>`;
  return h;
}

/* Group Settings modal. */

let _settingsGroup = null;
let _gsInitialTab = 'group';
let _gsInitialSubtab = '';
let _gsActiveSubTabs = {};
let _gsBoardSyncPreflightMode = '';
let _gsBoardSyncProjectOptions = [];
let _gsBoardSyncProjectsLoadedKey = '';

const DIGEST_VERBOSITY_TOOLTIP_HELP = 'Controls how much detail appears in digest events sent to this agent. Higher verbosity can wake the agent more often on coarse-event activity in the group.';

function _setHintText(id, text) {
  const el = document.getElementById(id);
  if (el) el.dataset.hint = text;
}

function _wireGroupSettingsTooltipText() {
  _setHintText('gs-engineer-digest-verbosity-hint', DIGEST_VERBOSITY_TOOLTIP_HELP);
  _setHintText('gs-architect-digest-verbosity-hint', DIGEST_VERBOSITY_TOOLTIP_HELP);
}

function _setEngineerWorkerVisibilityPermission(restrictToCreatedAgents) {
  // The stored setting keeps the legacy "hide other Engineers' workers"
  // polarity. The UI presents the inverse, affirmative permission.
  document.getElementById('gs-engineer-restrict-to-created-agents').checked = !restrictToCreatedAgents;
}

function _getEngineerRestrictToCreatedAgentsFromPermission() {
  return !document.getElementById('gs-engineer-restrict-to-created-agents').checked;
}

function _normalizeGsSelection(tab, subtab) {
  const rawTab = String(tab || 'group').trim() || 'group';
  const rawSubtab = String(subtab || '').trim();
  let nextTab = rawTab;
  let nextSubtab = rawSubtab;

  // Back-compat for callers or saved links that still use previous tab names:
  // manual terminal defaults are no longer an operator-visible settings pane,
  // while worker execution settings became the Workers tab.
  if (rawTab === 'agents') nextTab = 'workers';
  if (rawTab === 'terminals') {
    nextTab = 'group';
    nextSubtab = 'group-general';
  }
  if (rawSubtab === 'agent-terminals') {
    nextTab = 'group';
    nextSubtab = 'group-general';
  } else if (rawSubtab === 'agent-general') {
    nextSubtab = 'worker-execution';
  } else if (rawSubtab === 'agent-worktree') {
    nextSubtab = 'worker-worktree';
  } else if (rawSubtab === 'agent-notifications') {
    nextSubtab = 'worker-notifications';
  } else if (rawSubtab === 'group-terminals') {
    nextSubtab = 'group-general';
  }

  if (nextSubtab.indexOf('group-') === 0) nextTab = 'group';
  if (nextSubtab.indexOf('worker-') === 0) nextTab = 'workers';
  if (!['group', 'workers', 'engineer', 'architect'].includes(nextTab)) {
    nextTab = 'group';
  }
  return { tab: nextTab, subtab: nextSubtab };
}

function _boardSyncCommandPayload(cmd, args) {
  const payload = {
    cmd: cmd,
    args: Object.assign({}, args || {}),
  };
  args = args || {};
  for (const key in args) payload[key] = args[key];
  return payload;
}

function _gsStringifyJsonMap(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return '';
  const keys = Object.keys(value);
  if (!keys.length) return '';
  try {
    return JSON.stringify(value, null, 2);
  } catch (_e) {
    return '';
  }
}

function _gsParseJsonMap(id, label) {
  const el = document.getElementById(id);
  const raw = el ? String(el.value || '').trim() : '';
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('Expected a JSON object.');
    }
    const out = {};
    Object.keys(parsed).forEach(function(key) {
      const k = String(key || '').trim();
      if (!k) return;
      const value = parsed[key];
      if (value === null || value === undefined) return;
      const v = String(value).trim();
      if (v) out[k] = v;
    });
    return out;
  } catch (err) {
    const message = label + ' must be a valid JSON object.';
    if (typeof _showToast === 'function') _showToast(message, 'error');
    if (el && typeof el.focus === 'function') el.focus();
    return null;
  }
}

function _gsParseJsonMapSilent(id) {
  const el = document.getElementById(id);
  const raw = el ? String(el.value || '').trim() : '';
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {};
    }
    const out = {};
    Object.keys(parsed).forEach(function(key) {
      const k = String(key || '').trim();
      const value = parsed[key];
      const v = value === null || value === undefined ? '' : String(value).trim();
      if (k && v) out[k] = v;
    });
    return out;
  } catch (_err) {
    return {};
  }
}

function _gsBoardSyncMapConfig(kind) {
  return kind === 'assignee'
    ? {
        textareaId: 'gs-board-sync-github-assignee-map',
        hostId: 'gs-board-sync-assignee-map-editor',
        keyPlaceholder: 'Torque agent ID or slug',
        valuePlaceholder: 'GitHub login',
        empty: 'No assignee mappings yet.',
      }
    : {
        textareaId: 'gs-board-sync-github-lane-map',
        hostId: 'gs-board-sync-lane-map-editor',
        keyPlaceholder: 'Torque lane',
        valuePlaceholder: 'GitHub status',
        empty: 'No lane mappings yet.',
      };
}

function _gsBoardSyncMapSyncFromEditor(kind) {
  const config = _gsBoardSyncMapConfig(kind);
  const host = document.getElementById(config.hostId);
  const textarea = document.getElementById(config.textareaId);
  if (!host || !textarea || !host.querySelectorAll) return;
  const out = {};
  host.querySelectorAll('.settings-map-row').forEach(function(row) {
    const key = row.querySelector('.settings-map-key');
    const value = row.querySelector('.settings-map-value');
    const k = String(key && key.value || '').trim();
    const v = String(value && value.value || '').trim();
    if (k && v) out[k] = v;
  });
  textarea.value = Object.keys(out).length ? JSON.stringify(out, null, 2) : '';
}

function _gsBoardSyncMapAppendRow(kind, keyValue, mappedValue) {
  const config = _gsBoardSyncMapConfig(kind);
  const host = document.getElementById(config.hostId);
  if (!host || !host.appendChild || !document.createElement) return null;
  const row = document.createElement('div');
  row.className = 'settings-map-row';

  const key = document.createElement('input');
  key.type = 'text';
  key.className = 'settings-map-key';
  key.value = keyValue || '';
  key.placeholder = config.keyPlaceholder;
  key.setAttribute('aria-label', config.keyPlaceholder);

  const arrow = document.createElement('span');
  arrow.className = 'settings-map-arrow';
  arrow.textContent = '→';
  arrow.setAttribute('aria-hidden', 'true');

  const value = document.createElement('input');
  value.type = 'text';
  value.className = 'settings-map-value';
  value.value = mappedValue || '';
  value.placeholder = config.valuePlaceholder;
  value.setAttribute('aria-label', config.valuePlaceholder);

  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'settings-map-remove';
  remove.textContent = '×';
  remove.setAttribute('aria-label', 'Remove mapping');
  remove.onclick = function() {
    row.remove();
    _gsBoardSyncMapSyncFromEditor(kind);
    if (!host.querySelector('.settings-map-row')) gsBoardSyncMapRender(kind);
    if (typeof settingsShellMarkDirty === 'function') {
      settingsShellMarkDirty('modal-group-settings');
    }
  };
  key.oninput = value.oninput = function() { _gsBoardSyncMapSyncFromEditor(kind); };

  row.appendChild(key);
  row.appendChild(arrow);
  row.appendChild(value);
  row.appendChild(remove);
  host.appendChild(row);
  return key;
}

function gsBoardSyncMapRender(kind, options) {
  const config = _gsBoardSyncMapConfig(kind);
  const host = document.getElementById(config.hostId);
  const textarea = document.getElementById(config.textareaId);
  if (!host || !textarea || !host.replaceChildren || !document.createElement) return;
  host.replaceChildren();
  const raw = String(textarea.value || '').trim();
  let parsed = {};
  let valid = true;
  if (raw) {
    try {
      parsed = JSON.parse(raw);
      valid = !!parsed && typeof parsed === 'object' && !Array.isArray(parsed);
    } catch (_err) {
      valid = false;
    }
  }
  if (!valid) {
    const error = document.createElement('div');
    error.className = 'settings-map-empty settings-map-error';
    error.textContent = 'Raw JSON is invalid. Fix it in Advanced to restore the visual editor.';
    host.appendChild(error);
    return;
  }
  Object.keys(parsed).forEach(function(key) {
    _gsBoardSyncMapAppendRow(kind, key, parsed[key]);
  });
  if (options && options.appendBlank) {
    const input = _gsBoardSyncMapAppendRow(kind, '', '');
    if (input && input.focus) input.focus();
  }
  if (!host.querySelector('.settings-map-row')) {
    const empty = document.createElement('div');
    empty.className = 'settings-map-empty';
    empty.textContent = config.empty;
    host.appendChild(empty);
  }
}

function gsBoardSyncMapAddRow(kind) {
  const config = _gsBoardSyncMapConfig(kind);
  const host = document.getElementById(config.hostId);
  if (host && host.querySelector) {
    const empty = host.querySelector('.settings-map-empty');
    if (empty) empty.remove();
  }
  const input = _gsBoardSyncMapAppendRow(kind, '', '');
  if (input && input.focus) input.focus();
}

function _gsBoardSyncProjectNumberValue() {
  const el = document.getElementById('gs-board-sync-github-project-number');
  return parseInt(el && el.value, 10) || 0;
}

function _gsBoardSyncProjectIdValue() {
  const el = document.getElementById('gs-board-sync-github-project-id');
  return el ? String(el.value || '').trim() : '';
}

function _gsBoardSyncGithubSettingsFromForm() {
  return {
    github_repo: document.getElementById('gs-board-sync-github-repo').value.trim(),
    github_project_owner: document.getElementById('gs-board-sync-github-project-owner').value.trim(),
    github_project_number: _gsBoardSyncProjectNumberValue(),
    github_project_id: _gsBoardSyncProjectIdValue(),
    github_project_status_field: document.getElementById('gs-board-sync-github-status-field').value.trim() || 'Status',
    github_lane_status_map: _gsParseJsonMapSilent('gs-board-sync-github-lane-map'),
  };
}

function _gsBoardSyncDraftSettings() {
  const providerEl = document.getElementById('gs-board-sync-provider');
  const provider = providerEl ? String(providerEl.value || 'none') : 'none';
  return {
    board_sync_provider: provider,
    board_sync_enabled: document.getElementById('gs-board-sync-enabled').checked,
    board_sync_github: _gsBoardSyncGithubSettingsFromForm(),
  };
}

function onGsBoardSyncProviderChange(loadProjects) {
  const providerEl = document.getElementById('gs-board-sync-provider');
  const configEl = document.getElementById('gs-board-sync-github-config');
  const provider = providerEl ? String(providerEl.value || 'none') : 'none';
  if (configEl) configEl.style.display = provider === 'github' ? '' : 'none';
  if (provider === 'github' && loadProjects !== false) {
    _gsBoardSyncMaybeLoadProjects();
  }
}

function _gsBoardSyncSetPreflightStatus(kind, message) {
  const el = document.getElementById('gs-board-sync-preflight-summary');
  if (!el) return;
  el.textContent = message || '';
  el.className = 'board-sync-preflight-summary';
  if (kind) el.classList.add('board-sync-preflight-' + kind);
}

function _gsBoardSyncPreflightErrorText(msg) {
  const phase = String((msg && msg.phase) || '').trim();
  const raw = String(
    (msg && (msg.error || msg.message || msg.reason))
      || 'Board sync preflight failed.'
  ).trim();
  let text = raw || 'Board sync preflight failed.';
  const lower = text.toLowerCase();
  if (phase === 'project_scope'
      || lower.indexOf('project scope') >= 0
      || lower.indexOf("'project' scope") >= 0
      || lower.indexOf('gh auth refresh -s project') >= 0) {
    if (text.indexOf('gh auth refresh -s project') < 0) {
      text += ' Run: gh auth refresh -s project';
    }
  } else if (phase === 'repo'
      || lower.indexOf('repository') >= 0
      || lower.indexOf('repo') >= 0
      || lower.indexOf('not found') >= 0) {
    text += ' Check the repo field or click “Use current repo”.';
  }
  return text;
}

function _gsBoardSyncSetProjectStatus(kind, message) {
  const el = document.getElementById('gs-board-sync-project-summary');
  if (!el) return;
  el.textContent = message || '';
  el.className = 'board-sync-project-summary';
  if (kind) el.classList.add('board-sync-preflight-' + kind);
}

function _gsBoardSyncProjectValue(project) {
  if (!project || typeof project !== 'object') return '';
  const owner = String(project.owner || '').trim();
  const number = parseInt(project.number, 10) || 0;
  const id = String(project.id || '').trim();
  return owner + '#' + number + '#' + id;
}

function _gsBoardSyncProjectLabel(project) {
  const number = parseInt(project.number, 10) || 0;
  const name = String(project.name || project.title || '').trim() || 'Untitled project';
  const owner = String(project.owner || '').trim() || 'unknown owner';
  return owner + ' · #' + (number || '?') + ' — ' + name;
}

function _gsBoardSyncFindProject(value) {
  const wanted = String(value || '');
  return (_gsBoardSyncProjectOptions || []).find(function(project) {
    return _gsBoardSyncProjectValue(project) === wanted;
  }) || null;
}

function _gsBoardSyncRenderProjectOptions() {
  const select = document.getElementById('gs-board-sync-github-project-select');
  if (!select) return;
  const ownerEl = document.getElementById('gs-board-sync-github-project-owner');
  const numberEl = document.getElementById('gs-board-sync-github-project-number');
  const idEl = document.getElementById('gs-board-sync-github-project-id');
  const selectedOwner = ownerEl ? String(ownerEl.value || '').trim() : '';
  const selectedNumber = parseInt(numberEl && numberEl.value, 10) || 0;
  const selectedId = idEl ? String(idEl.value || '').trim() : '';
  select.innerHTML = '';

  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = 'Select a GitHub Project…';
  select.appendChild(placeholder);

  let selectedValue = '';
  (_gsBoardSyncProjectOptions || []).forEach(function(project) {
    const option = document.createElement('option');
    const value = _gsBoardSyncProjectValue(project);
    option.value = value;
    option.textContent = _gsBoardSyncProjectLabel(project);
    option.dataset.owner = String(project.owner || '');
    option.dataset.number = String(project.number || '');
    option.dataset.projectId = String(project.id || '');
    select.appendChild(option);
    const sameId = selectedId && String(project.id || '') === selectedId;
    const sameOwnerNumber = selectedNumber
      && String(project.owner || '').toLowerCase() === selectedOwner.toLowerCase()
      && parseInt(project.number, 10) === selectedNumber;
    if (sameId || sameOwnerNumber) selectedValue = value;
  });

  if (!selectedValue && selectedOwner && selectedNumber) {
    const manualProject = {
      owner: selectedOwner,
      number: selectedNumber,
      id: selectedId,
      name: 'Configured manually',
    };
    selectedValue = _gsBoardSyncProjectValue(manualProject);
    const option = document.createElement('option');
    option.value = selectedValue;
    option.textContent = _gsBoardSyncProjectLabel(manualProject);
    select.appendChild(option);
  }
  select.value = selectedValue;
}

function _gsBoardSyncProjectsKey() {
  const providerEl = document.getElementById('gs-board-sync-provider');
  const repoEl = document.getElementById('gs-board-sync-github-repo');
  const ownerEl = document.getElementById('gs-board-sync-github-project-owner');
  const provider = providerEl ? String(providerEl.value || 'none') : 'none';
  const repo = repoEl ? String(repoEl.value || '').trim() : '';
  const owner = ownerEl ? String(ownerEl.value || '').trim() : '';
  return [_settingsGroup || '', provider, repo, owner].join('|');
}

function _gsBoardSyncMaybeLoadProjects(force) {
  if (!_settingsGroup) return;
  const providerEl = document.getElementById('gs-board-sync-provider');
  const provider = providerEl ? String(providerEl.value || 'none') : 'none';
  if (provider !== 'github') return;
  const key = _gsBoardSyncProjectsKey();
  if (!force && key && key === _gsBoardSyncProjectsLoadedKey) return;
  gsBoardSyncReloadProjects();
}

function gsBoardSyncReloadProjects() {
  if (!_settingsGroup) return;
  const providerEl = document.getElementById('gs-board-sync-provider');
  const provider = providerEl ? String(providerEl.value || 'none') : 'none';
  if (provider !== 'github') {
    _gsBoardSyncSetProjectStatus('error', 'Select GitHub as the provider to load projects.');
    return;
  }
  const ownerEl = document.getElementById('gs-board-sync-github-project-owner');
  const owner = ownerEl ? String(ownerEl.value || '').trim() : '';
  _gsBoardSyncProjectsLoadedKey = _gsBoardSyncProjectsKey();
  _gsBoardSyncSetProjectStatus('pending', 'Loading accessible GitHub Projects…');
  send(_boardSyncCommandPayload('board_sync_list_projects', {
    group: _settingsGroup,
    provider: provider,
    owner: owner,
    settings: _gsBoardSyncDraftSettings(),
  }));
}

function _handleBoardSyncProjects(msg) {
  if (!msg || msg.type !== 'board_sync_list_projects') return;
  const group = String(msg.group || '');
  if (_settingsGroup && group && group !== _settingsGroup) return;
  if (!msg.ok) {
    _gsBoardSyncSetProjectStatus(
      'error',
      msg.error || 'Could not load GitHub Projects.'
    );
    return;
  }
  _gsBoardSyncProjectOptions = Array.isArray(msg.projects) ? msg.projects.slice() : [];
  _gsBoardSyncRenderProjectOptions();
  if (_gsBoardSyncProjectOptions.length) {
    const owners = Array.isArray(msg.owners) ? msg.owners.filter(Boolean) : [];
    let loadedMessage = 'Loaded ' + _gsBoardSyncProjectOptions.length + ' accessible project'
      + (_gsBoardSyncProjectOptions.length === 1 ? '' : 's');
    if (owners.length) loadedMessage += ' from ' + owners.join(', ');
    loadedMessage += '.';
    if (Array.isArray(msg.errors) && msg.errors.length) {
      loadedMessage += ' Some owners could not be checked.';
    }
    _gsBoardSyncSetProjectStatus(
      'ok',
      loadedMessage
    );
  } else {
    _gsBoardSyncSetProjectStatus(
      'error',
      'No accessible projects — verify gh auth scope or try Other owner…'
    );
  }
}

function onGsBoardSyncProjectSelect() {
  const select = document.getElementById('gs-board-sync-github-project-select');
  if (!select) return;
  const project = _gsBoardSyncFindProject(select.value);
  if (!project) return;
  document.getElementById('gs-board-sync-github-project-owner').value =
    String(project.owner || '').trim();
  document.getElementById('gs-board-sync-github-project-number').value =
    parseInt(project.number, 10) || '';
  const idEl = document.getElementById('gs-board-sync-github-project-id');
  if (idEl) idEl.value = String(project.id || '').trim();
  _gsBoardSyncPreflightMode = 'project-select';
  _gsBoardSyncSetProjectStatus('pending', 'Resolving project Status options…');
  _gsBoardSyncSetPreflightStatus('pending', 'Resolving GitHub Project…');
  send(_boardSyncCommandPayload('board_sync_preflight', {
    group: _settingsGroup,
    provider: 'github',
    settings: _gsBoardSyncDraftSettings(),
  }));
}

function _gsBoardSyncApplyLaneMapSuggestion(msg) {
  const textarea = document.getElementById('gs-board-sync-github-lane-map');
  if (!textarea) return false;
  if (String(textarea.value || '').trim()) return false;
  const suggestion = msg && msg.lane_status_map_suggestion;
  if (!suggestion || typeof suggestion !== 'object' || Array.isArray(suggestion)) {
    return false;
  }
  const keys = Object.keys(suggestion).filter(function(key) {
    return String(key || '').trim() && String(suggestion[key] || '').trim();
  });
  const unmatched = Array.isArray(msg.lane_status_map_unmatched_lanes)
    ? msg.lane_status_map_unmatched_lanes.filter(Boolean)
    : [];
  if (!keys.length) {
    if (unmatched.length) {
      _gsBoardSyncSetProjectStatus(
        'pending',
        'No automatic lane matches. Map these lanes manually: '
          + unmatched.join(', ') + '.'
      );
    }
    return false;
  }
  const out = {};
  keys.forEach(function(key) {
    out[String(key).trim()] = String(suggestion[key]).trim();
  });
  textarea.value = JSON.stringify(out, null, 2);
  gsBoardSyncMapRender('lane');
  const strategy = String(msg.lane_status_map_strategy || '').trim();
  let message = strategy === 'position'
    ? 'Auto-filled lane → status mapping by lane/status position.'
    : 'Auto-filled matching lane → status names.';
  if (unmatched.length) message += ' Review unmatched lanes: ' + unmatched.join(', ') + '.';
  _gsBoardSyncSetProjectStatus(unmatched.length ? 'pending' : 'ok', message);
  return true;
}

function testGroupBoardSyncConnection() {
  if (!_settingsGroup) return;
  _gsBoardSyncPreflightMode = 'test';
  _gsBoardSyncSetPreflightStatus('pending', 'Testing GitHub connection…');
  send(_boardSyncCommandPayload('board_sync_preflight', {
    group: _settingsGroup,
    provider: document.getElementById('gs-board-sync-provider').value || 'none',
    settings: _gsBoardSyncDraftSettings(),
  }));
}

function gsBoardSyncUseCurrentRepo() {
  if (!_settingsGroup) return;
  _gsBoardSyncPreflightMode = 'use-current-repo';
  _gsBoardSyncSetPreflightStatus('pending', 'Inspecting current GitHub repo…');
  send(_boardSyncCommandPayload('board_sync_preflight', {
    group: _settingsGroup,
    provider: document.getElementById('gs-board-sync-provider').value || 'none',
    settings: _gsBoardSyncDraftSettings(),
  }));
}

function _handleBoardSyncPreflight(msg) {
  if (!msg || msg.type !== 'board_sync_preflight') return;
  const group = String(msg.group || '');
  if (_settingsGroup && group && group !== _settingsGroup) return;
  const mode = _gsBoardSyncPreflightMode;
  _gsBoardSyncPreflightMode = '';
  if (msg.ok) {
    const repo = String(msg.repo || msg.repository || '').trim();
    if (mode === 'use-current-repo' && repo) {
      const repoEl = document.getElementById('gs-board-sync-github-repo');
      if (repoEl) repoEl.value = repo;
    }
    if (msg.project_owner) {
      const ownerEl = document.getElementById('gs-board-sync-github-project-owner');
      if (ownerEl) ownerEl.value = String(msg.project_owner || '');
    }
    if (msg.project_number) {
      const numberEl = document.getElementById('gs-board-sync-github-project-number');
      if (numberEl) numberEl.value = msg.project_number;
    }
    if (msg.project_id) {
      const idEl = document.getElementById('gs-board-sync-github-project-id');
      if (idEl) idEl.value = String(msg.project_id || '');
    }
    _gsBoardSyncApplyLaneMapSuggestion(msg);
    let success = 'GitHub connection OK';
    if (repo) success += ': ' + repo;
    if (msg.project_number || msg.project_id) success += ' · Project OK';
    _gsBoardSyncSetPreflightStatus('ok', success);
    if (typeof _showToast === 'function') _showToast('Board sync connection OK', 'success');
    return;
  }
  const errorText = _gsBoardSyncPreflightErrorText(msg);
  _gsBoardSyncSetPreflightStatus('error', errorText);
  if (typeof _showToast === 'function') _showToast(errorText, 'error');
}

function switchGsTab(name) {
  name = _normalizeGsSelection(name, '').tab;
  document.querySelectorAll('#modal-group-settings .gs-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('#modal-group-settings .gs-pane').forEach(p =>
    p.classList.toggle('active', p.dataset.pane === name));
  const pane = document.querySelector(`#modal-group-settings .gs-pane[data-pane="${name}"]`);
  if (!pane) return;
  const preferredSubtab = _gsActiveSubTabs[name] || '';
  const subtabs = Array.prototype.slice.call(pane.querySelectorAll('.gs-subtab'));
  let nextSubtab = null;
  if (preferredSubtab) {
    nextSubtab = subtabs.find(t => t.dataset && t.dataset.subtab === preferredSubtab) || null;
  }
  if (!nextSubtab) {
    nextSubtab = pane.querySelector('.gs-subtab');
  }
  if (nextSubtab) switchGsSubTab(name, nextSubtab);
  if (typeof settingsShellSyncView === 'function') {
    settingsShellSyncView('modal-group-settings');
  }
}

function switchGsSubTab(pane, btn) {
  const container = btn.closest('.gs-pane');
  container.querySelectorAll('.gs-subtab').forEach(t =>
    t.classList.toggle('active', t === btn));
  const target = btn.dataset.subtab;
  container.querySelectorAll('.gs-subpane').forEach(p =>
    p.classList.toggle('active', p.dataset.subpane === target));
  const paneName = container.dataset && container.dataset.pane
    ? container.dataset.pane
    : pane;
  if (paneName && target) _gsActiveSubTabs[paneName] = target;
  if (target === 'group-sync') _gsBoardSyncMaybeLoadProjects();
  if (typeof settingsShellSyncView === 'function') {
    settingsShellSyncView('modal-group-settings');
  }
}

function openGroupSettings(group, initialTab, initialSubtab) {
  _settingsGroup = group;
  const selection = _normalizeGsSelection(initialTab || 'group', initialSubtab || '');
  _gsInitialTab = selection.tab;
  _gsInitialSubtab = selection.subtab;
  send({ cmd: 'get_group_settings', group });
}

let _systemPromptPreviewSeq = 0;
let _systemPromptPreviewRequestId = '';

function _groupSettingsPromptPreviewPayload() {
  return {
    default_agent_template: document.getElementById('gs-default-agent-template').value,
    agent_provider: _getProviderValue('gs-agent-provider'),
    agent_boot_command: document.getElementById('gs-agent-boot-cmd').value.trim(),
    agent_model: document.getElementById('gs-agent-model').value.trim(),
    agent_reasoning_effort: document.getElementById('gs-agent-reasoning-effort').value,
    worker_provider: _getProviderValue('gs-worker-provider'),
    worker_boot_command: document.getElementById('gs-worker-boot-command').value.trim(),
    worker_model: document.getElementById('gs-worker-model').value.trim(),
    worker_reasoning_effort: document.getElementById('gs-worker-reasoning-effort').value,
    agent_directory: document.getElementById('gs-agent-directory').value.trim(),
    agent_shell: document.getElementById('gs-agent-shell').value,
    engineer_merge_mode: document.getElementById('gs-engineer-merge-mode').value,
    worktree_merge_cleanup: document.getElementById('gs-wt-merge-cleanup').value,
    default_engineer_specializations: (_gsEngineerSpecs || []).slice(),
  };
}

function _engineerSettingsPromptPreviewPayload() {
  return {
    engineer_provider: _getProviderValue('gs-engineer-provider'),
    engineer_boot_command: document.getElementById('gs-engineer-boot-cmd').value.trim(),
    engineer_model: document.getElementById('gs-engineer-model').value.trim(),
    engineer_reasoning_effort: document.getElementById('gs-engineer-reasoning-effort').value,
    engineer_directory: document.getElementById('gs-engineer-directory').value.trim(),
    engineer_shell: document.getElementById('gs-engineer-shell').value,
    custom_instructions: document.getElementById('gs-engineer-custom-instructions').value,
    restrict_to_created_agents: _getEngineerRestrictToCreatedAgentsFromPermission(),
    autonomy_mode: document.getElementById('gs-engineer-autonomy-mode').value,
    default_worker_concurrency: parseInt(
      document.getElementById('gs-engineer-default-worker-concurrency').value,
      10
    ) || 2,
    wave_size_preference: document.getElementById('gs-engineer-wave-size-preference').value,
    same_agent_follow_up_preference: document.getElementById('gs-engineer-same-agent-follow-up-preference').value,
    digest_verbosity: document.getElementById('gs-engineer-digest-verbosity').value,
    escalation_style: document.getElementById('gs-engineer-escalation-style').value,
  };
}

function _architectSettingsPromptPreviewPayload() {
  return {
    architect_provider: _getProviderValue('gs-architect-provider'),
    architect_boot_command: document.getElementById('gs-architect-boot-cmd').value.trim(),
    architect_model: document.getElementById('gs-architect-model').value.trim(),
    architect_reasoning_effort: document.getElementById('gs-architect-reasoning-effort').value,
    architect_directory: document.getElementById('gs-architect-directory').value.trim(),
    architect_shell: document.getElementById('gs-architect-shell').value,
    architect_custom_instructions: document.getElementById('gs-architect-custom-instructions').value,
    architect_autonomy_mode: document.getElementById('gs-architect-autonomy-mode').value,
    architect_digest_verbosity: document.getElementById('gs-architect-digest-verbosity').value,
    architect_journal_checkpoint_frequency: document.getElementById('gs-architect-journal-checkpoint').value.trim() || 'every_10_actions',
  };
}

function _systemPromptPreviewTitle(kind) {
  return kind === 'architect'
    ? 'Architect system prompt'
    : 'Engineer system prompt';
}

function _clearSystemPromptPreviewError() {
  const errorEl = document.getElementById('system-prompt-preview-error');
  if (!errorEl) return;
  errorEl.textContent = '';
  errorEl.style.display = 'none';
}

function _formatSystemPromptPreviewError(msg) {
  const reason = String(
    (msg && (msg.message || msg.error || msg.reason || msg.detail))
      || 'Unknown error'
  ).trim() || 'Unknown error';
  if (reason.indexOf('Failed to render system prompt:') === 0) {
    return reason;
  }
  return `Failed to render system prompt: ${reason}`;
}

function _systemPromptPreviewResponseIsCurrent(msg) {
  return !(
    msg
    && msg.request_id
    && _systemPromptPreviewRequestId
    && msg.request_id !== _systemPromptPreviewRequestId
  );
}

function _showSystemPromptPreviewError(msg) {
  if (!_systemPromptPreviewResponseIsCurrent(msg)) return false;
  const modal = document.getElementById('modal-system-prompt-preview');
  const contentEl = document.getElementById('system-prompt-preview-content');
  const errorEl = document.getElementById('system-prompt-preview-error');
  if (!modal || !contentEl || !errorEl) return false;
  const cmd = String((msg && (msg.cmd || msg.command || msg.request_cmd)) || '').trim();
  const hasExplicitPreviewSignal = !!(
    (msg && msg.request_id)
    || cmd === 'preview_system_prompt'
  );
  const hasPreviewRequest = hasExplicitPreviewSignal
    || !!(_systemPromptPreviewRequestId && contentEl.textContent === 'Loading…');
  if (!hasPreviewRequest) return false;

  contentEl.textContent = '';
  document.getElementById('system-prompt-preview-summary').textContent =
    'Unable to render system prompt.';
  errorEl.textContent = _formatSystemPromptPreviewError(msg);
  errorEl.style.display = '';
  const copyBtn = document.getElementById('system-prompt-preview-copy-btn');
  if (copyBtn) {
    copyBtn.disabled = true;
    copyBtn.textContent = 'Copy to clipboard';
  }
  return true;
}

function openGroupSystemPromptPreview(kind) {
  const previewKind = String(kind || '').trim().toLowerCase() === 'architect'
    ? 'architect'
    : 'engineer';
  const modal = document.getElementById('modal-system-prompt-preview');
  if (!modal || !_settingsGroup) return;

  const requestId = `system-prompt-preview-${Date.now()}-${++_systemPromptPreviewSeq}`;
  _systemPromptPreviewRequestId = requestId;
  document.getElementById('system-prompt-preview-title').textContent =
    _systemPromptPreviewTitle(previewKind);
  document.getElementById('system-prompt-preview-summary').textContent =
    `Rendering ${previewKind} prompt from the current unsaved form values…`;
  document.getElementById('system-prompt-preview-content').textContent = 'Loading…';
  _clearSystemPromptPreviewError();
  const copyBtn = document.getElementById('system-prompt-preview-copy-btn');
  if (copyBtn) {
    copyBtn.disabled = true;
    copyBtn.textContent = 'Copy to clipboard';
  }

  if (typeof openNestedModal === 'function') {
    openNestedModal('modal-system-prompt-preview');
  } else {
    modal.classList.add('visible');
  }

  send({
    cmd: 'preview_system_prompt',
    request_id: requestId,
    group: _settingsGroup,
    kind: previewKind,
    group_settings: _groupSettingsPromptPreviewPayload(),
    settings: previewKind === 'architect'
      ? _architectSettingsPromptPreviewPayload()
      : _engineerSettingsPromptPreviewPayload(),
  });
}

function closeSystemPromptPreview() {
  _systemPromptPreviewRequestId = '';
  if (typeof closeNestedModal === 'function'
      && closeNestedModal('modal-system-prompt-preview')) {
    return;
  }
  const modal = document.getElementById('modal-system-prompt-preview');
  if (modal) {
    modal.classList.remove('visible');
    modal.classList.remove('modal-nested');
  }
}

function _showSystemPromptPreview(msg) {
  msg = msg || {};
  if (!_systemPromptPreviewResponseIsCurrent(msg)) return;
  if (msg.error || msg.message || msg.reason || msg.detail) {
    _showSystemPromptPreviewError(msg);
    return;
  }
  const kind = String((msg && msg.kind) || '').trim().toLowerCase();
  if (kind) {
    document.getElementById('system-prompt-preview-title').textContent =
      _systemPromptPreviewTitle(kind);
  }
  const prompt = (msg && msg.prompt) || '(empty)';
  document.getElementById('system-prompt-preview-content').textContent = prompt;
  _clearSystemPromptPreviewError();
  const label = kind || 'system';
  document.getElementById('system-prompt-preview-summary').textContent =
    `Rendered ${label} prompt for ${msg.group || _settingsGroup || 'this group'}.`;
  const copyBtn = document.getElementById('system-prompt-preview-copy-btn');
  if (copyBtn) copyBtn.disabled = false;
}

function copySystemPromptPreview() {
  const text = document.getElementById('system-prompt-preview-content').textContent;
  const btn = document.getElementById('system-prompt-preview-copy-btn');
  if (typeof navigator === 'undefined'
      || !navigator.clipboard
      || !navigator.clipboard.writeText) return;
  navigator.clipboard.writeText(text).then(function() {
    if (!btn) return;
    btn.textContent = 'Copied!';
    setTimeout(function() { btn.textContent = 'Copy to clipboard'; }, 1500);
  });
}

async function deleteSettingsGroup() {
  const group = _settingsGroup;
  if (!group || typeof removeGroup !== 'function') return;
  if (await removeGroup(group)) {
    _settingsGroup = null;
    closeModals();
  }
}

function _envToText(obj) {
  return Object.entries(obj || {}).map(([k, v]) => k + '=' + v).join('\n');
}

function _textToEnv(id) {
  const text = document.getElementById(id).value.trim();
  const env = {};
  if (text) {
    for (const line of text.split('\n')) {
      const eq = line.indexOf('=');
      if (eq > 0) env[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
    }
  }
  return env;
}

function _setSelectValue(id, value, fallback) {
  const el = document.getElementById(id);
  if (!el) return;
  el.value = value != null && value !== '' ? String(value) : String(fallback);
}

function _autoGrowTextArea(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = Math.max(el.scrollHeight || 0, 110) + 'px';
}

const _ENGINEER_NOTIFICATION_PRESETS = {
  quiet: {
    label: 'Quiet',
    description: 'Major milestones only, slower digests, and no idle heartbeat.',
    digest_verbosity: 'compact',
    push_interval: 120,
    max_interval: 600,
    heartbeat_interval: 0,
    enabled_events: ['task_derived', 'task_health_alert'],
  },
  normal: {
    label: 'Normal',
    description: 'Balanced Torque defaults with key lifecycle updates and heartbeats.',
    digest_verbosity: 'balanced',
    push_interval: 60,
    max_interval: 300,
    heartbeat_interval: 300,
    enabled_events: [
      'agent_started',
      'task_dispatched',
      'task_derived',
      'task_health_alert',
    ],
  },
  noisy: {
    label: 'Noisy',
    description: 'Faster, more detailed digests including ongoing progress updates.',
    digest_verbosity: 'detailed',
    push_interval: 30,
    max_interval: 120,
    heartbeat_interval: 60,
    enabled_events: [
      'agent_started',
      'task_dispatched',
      'task_derived',
      'agent_progress',
      'task_health_alert',
    ],
  },
};

function _defaultEngineerNotificationSettings() {
  const preset = _ENGINEER_NOTIFICATION_PRESETS.normal;
  return {
    engineer_can_override_worker_provider: true,
    digest_verbosity: preset.digest_verbosity,
    push_interval: preset.push_interval,
    max_interval: preset.max_interval,
    heartbeat_interval: preset.heartbeat_interval,
    enabled_events: preset.enabled_events.slice(),
  };
}

function _getEngineerNotificationPresetSettings(name) {
  const preset = _ENGINEER_NOTIFICATION_PRESETS[String(name || '').trim().toLowerCase()];
  if (!preset) return _defaultEngineerNotificationSettings();
  return {
    digest_verbosity: preset.digest_verbosity,
    push_interval: preset.push_interval,
    max_interval: preset.max_interval,
    heartbeat_interval: preset.heartbeat_interval,
    enabled_events: preset.enabled_events.slice(),
  };
}

function _sortedEngineerEvents(events) {
  return Array.from(new Set((events || []).map((value) => String(value || ''))))
    .filter(Boolean)
    .sort();
}

function _matchEngineerNotificationPreset(settings) {
  const current = settings || {};
  const digestVerbosity = String(
    current.digest_verbosity != null && current.digest_verbosity !== ''
      ? current.digest_verbosity
      : 'balanced'
  );
  const pushInterval = parseInt(current.push_interval, 10);
  const maxInterval = parseInt(current.max_interval, 10);
  const heartbeatInterval = parseInt(current.heartbeat_interval, 10);
  const enabledEvents = _sortedEngineerEvents(current.enabled_events);

  for (const [name, preset] of Object.entries(_ENGINEER_NOTIFICATION_PRESETS)) {
    if (
      digestVerbosity === preset.digest_verbosity
      && pushInterval === preset.push_interval
      && maxInterval === preset.max_interval
      && heartbeatInterval === preset.heartbeat_interval
      && JSON.stringify(enabledEvents) === JSON.stringify(_sortedEngineerEvents(preset.enabled_events))
    ) {
      return name;
    }
  }
  return 'custom';
}

function _setEngineerNotificationPresetHint(id, presetName) {
  const el = document.getElementById(id);
  if (!el) return;
  const preset = _ENGINEER_NOTIFICATION_PRESETS[presetName];
  if (preset) {
    el.textContent = `${preset.label}: ${preset.description} Manual tweaks switch this to Custom.`;
    return;
  }
  el.textContent = 'Custom detailed settings. Pick a preset to overwrite the detailed notification controls below.';
}

function _groupFormEngineerNotificationSettings() {
  return {
    digest_verbosity: document.getElementById('gs-engineer-digest-verbosity').value,
    push_interval: parseInt(document.getElementById('gs-engineer-push-interval').value, 10) || 60,
    max_interval: parseInt(document.getElementById('gs-engineer-max-interval').value, 10) || 300,
    heartbeat_interval: parseInt(document.getElementById('gs-engineer-heartbeat-interval').value, 10),
    enabled_events: _getEngineerEnabledEvents(),
  };
}

function _applyGsEngineerNotificationPreset(name) {
  const preset = _getEngineerNotificationPresetSettings(name);
  _setSelectValue('gs-engineer-digest-verbosity', preset.digest_verbosity, 'balanced');
  _setSelectValue('gs-engineer-push-interval', preset.push_interval, 60);
  _setSelectValue('gs-engineer-max-interval', preset.max_interval, 300);
  _setSelectValue('gs-engineer-heartbeat-interval', preset.heartbeat_interval, 300);
  _setEngineerEventCheckboxes(preset.enabled_events);
}

function syncGsEngineerNotificationPreset() {
  const preset = _matchEngineerNotificationPreset(_groupFormEngineerNotificationSettings());
  _setSelectValue('gs-engineer-notification-preset', preset, 'custom');
  _setEngineerNotificationPresetHint('gs-engineer-notification-preset-hint', preset);
}

function onGsEngineerNotificationPresetChange() {
  const el = document.getElementById('gs-engineer-notification-preset');
  if (!el) return;
  const preset = el.value;
  if (preset && preset !== 'custom') {
    _applyGsEngineerNotificationPreset(preset);
  }
  syncGsEngineerNotificationPreset();
}

function _setEngineerEventCheckboxes(enabled) {
  const current = new Set(enabled || []);
  document.getElementById('gs-engineer-event-agent-started').checked = current.has('agent_started');
  document.getElementById('gs-engineer-event-task-dispatched').checked = current.has('task_dispatched');
  document.getElementById('gs-engineer-event-task-derived').checked = current.has('task_derived');
  document.getElementById('gs-engineer-event-agent-progress').checked = current.has('agent_progress');
  document.getElementById('gs-engineer-event-task-health-alert').checked = current.has('task_health_alert');
}

function _getEngineerEnabledEvents() {
  const events = [];
  if (document.getElementById('gs-engineer-event-agent-started').checked) events.push('agent_started');
  if (document.getElementById('gs-engineer-event-task-dispatched').checked) events.push('task_dispatched');
  if (document.getElementById('gs-engineer-event-task-derived').checked) events.push('task_derived');
  if (document.getElementById('gs-engineer-event-agent-progress').checked) events.push('agent_progress');
  if (document.getElementById('gs-engineer-event-task-health-alert').checked) events.push('task_health_alert');
  return events;
}

function _resetGsSubTabs(paneName, subtabName) {
  const btn = document.querySelector(`.gs-pane[data-pane="${paneName}"] .gs-subtab[data-subtab="${subtabName}"]`);
  if (btn) switchGsSubTab(paneName, btn);
}

function _resetGsEngineerSections() {
  _resetGsSubTabs('engineer', 'engineer-general');
}

/* -- Group Settings: Engineer-tab default specializations picker -------- */
let _gsEngineerSpecs = [];

function _gsEngineerAvailableSpecs() {
  return (state.specializations || [])
    .map(function (s) { return s && s.name; })
    .filter(Boolean);
}

function renderGsEngineerSpecializations() {
  const selectedEl = document.getElementById('gs-engineer-specializations-selected');
  const availableEl = document.getElementById('gs-engineer-specializations-available');
  if (!selectedEl || !availableEl) return;
  const selected = _gsEngineerSpecs;
  selectedEl.innerHTML = '';
  selected.forEach(function (name, idx) {
    const li = document.createElement('li');
    li.className = 'specialization-entry';
    const tag = idx === 0 ? ' (primary)' : '';
    const label = document.createElement('span');
    label.textContent = name + tag;
    li.appendChild(label);
    const controls = document.createElement('span');
    controls.className = 'specialization-controls-row';
    if (idx > 0) {
      const up = document.createElement('button');
      up.type = 'button'; up.textContent = '↑'; up.title = 'Move up';
      up.onclick = function () { gsEngineerMoveSpecialization(idx, -1); };
      controls.appendChild(up);
    }
    if (idx < selected.length - 1) {
      const down = document.createElement('button');
      down.type = 'button'; down.textContent = '↓'; down.title = 'Move down';
      down.onclick = function () { gsEngineerMoveSpecialization(idx, 1); };
      controls.appendChild(down);
    }
    const remove = document.createElement('button');
    remove.type = 'button'; remove.textContent = '×'; remove.title = 'Delete';
    remove.onclick = function () { gsEngineerRemoveSpecialization(idx); };
    controls.appendChild(remove);
    li.appendChild(controls);
    selectedEl.appendChild(li);
  });

  const available = _gsEngineerAvailableSpecs();
  availableEl.innerHTML = '';
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = available.length ? 'Pick a specialization...' : 'No specializations available';
  availableEl.appendChild(placeholder);
  available.forEach(function (name) {
    if (selected.indexOf(name) >= 0) return;
    const opt = document.createElement('option');
    opt.value = name; opt.textContent = name;
    const meta = (state.specializations || []).find(function (s) {
      return s && s.name === name;
    });
    if (meta && meta.preamble) opt.title = String(meta.preamble).slice(0, 200);
    availableEl.appendChild(opt);
  });
}

function gsEngineerAddSpecialization() {
  const availableEl = document.getElementById('gs-engineer-specializations-available');
  if (!availableEl) return;
  const name = availableEl.value;
  if (!name) return;
  if (_gsEngineerSpecs.indexOf(name) < 0) _gsEngineerSpecs.push(name);
  renderGsEngineerSpecializations();
}

function gsEngineerRemoveSpecialization(idx) {
  if (idx < 0 || idx >= _gsEngineerSpecs.length) return;
  _gsEngineerSpecs.splice(idx, 1);
  renderGsEngineerSpecializations();
}

function gsEngineerMoveSpecialization(idx, delta) {
  const newIdx = idx + delta;
  if (newIdx < 0 || newIdx >= _gsEngineerSpecs.length) return;
  const moved = _gsEngineerSpecs.splice(idx, 1)[0];
  _gsEngineerSpecs.splice(newIdx, 0, moved);
  renderGsEngineerSpecializations();
}

function openGsEngineerNewSpecializationDialog() {
  const modal = document.getElementById('modal-new-specialization');
  if (!modal) return;
  document.getElementById('new-specialization-name').value = '';
  document.getElementById('new-specialization-description').value = '';
  document.getElementById('new-specialization-preamble').value = '';
  document.getElementById('new-specialization-priorities').value = '';
  document.getElementById('new-specialization-scope').value = 'project';
  // Stash a flag so the submit handler knows to refresh the GS picker too.
  modal.dataset.gsEngineerSource = '1';
  if (typeof openNestedModal === 'function') {
    openNestedModal('modal-new-specialization');
  } else {
    modal.classList.add('visible');
  }
  document.getElementById('new-specialization-name').focus();
}

const _ARCHITECT_DIGEST_MANDATORY_EVENTS = [
  'ask_created',
  'engineer_awaiting_human_input',
  'agent_error',
  'agent_blocked',
  'task_blocked',
];

const _ARCHITECT_DIGEST_EVENT_CATALOG = [
  'task_done',
  'task_blocked',
  'task_error',
  'task_ask',
  'task_derive',
  'task_completed',
  'agent_blocked',
  'agent_error',
  'ask_created',
  'task_derived',
  'pipeline_complete',
  'engineer_hired',
  'engineer_fired',
  'engineer_dismissed',
  'engineer_rehired',
  'workflow_breach',
  'engineer_queue_empty',
  'perceived_empty_episode',
  'engineer_awaiting_human_input',
  'engineer_ask_resolved',
];

const _ARCHITECT_DIGEST_DEFAULT_EVENTS = [];

const _ARCHITECT_JOURNAL_CHECKPOINT_OPTIONS = [
  'every_5_actions',
  'every_10_actions',
  'every_15_actions',
  'every_20_actions',
  'every_20_minutes',
  'every_30_minutes',
  'every_60_minutes',
  'manual_only',
];

function _defaultArchitectSettings() {
  return {
    architect_boot_command: '',
    architect_provider: '',
    architect_model: '',
    architect_reasoning_effort: '',
    architect_directory: '',
    architect_shell: '',
    architect_custom_instructions: '',
    architect_autonomy_mode: 'dispatch_after_confirm',
    architect_digest_verbosity: 'balanced',
    architect_push_interval: 300,
    architect_max_interval: 600,
    architect_heartbeat_interval: 0,
    architect_suppress_empty_digests: true,
    architect_enabled_events: _ARCHITECT_DIGEST_DEFAULT_EVENTS.slice(),
    architect_journal_checkpoint_frequency: 'every_10_actions',
  };
}

function _architectJournalCheckpointLabel(value) {
  const raw = String(value || '').trim();
  if (raw === 'manual_only') return 'Manual only';
  const match = raw.match(/^every_([1-9]\d*)_(actions|minutes)$/);
  if (match) {
    const count = parseInt(match[1], 10);
    const unit = match[2] === 'actions' ? 'action' : 'minute';
    return `Every ${count} ${unit}${count === 1 ? '' : 's'}`;
  }
  return raw || 'Every 10 actions';
}

function _populateArchitectJournalCheckpointSelect(currentValue) {
  const sel = document.getElementById('gs-architect-journal-checkpoint');
  if (!sel) return;
  const current = String(currentValue || 'every_10_actions').trim() || 'every_10_actions';
  const values = _ARCHITECT_JOURNAL_CHECKPOINT_OPTIONS.slice();
  if (values.indexOf(current) < 0) values.push(current);
  sel.innerHTML = '';
  values.forEach((value) => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = _architectJournalCheckpointLabel(value);
    sel.appendChild(opt);
  });
  sel.value = current;
}

function _renderArchitectEventCheckboxes(enabled) {
  const grid = document.getElementById('gs-architect-events-grid');
  if (!grid) return;
  const set = new Set((enabled || []).map((value) => String(value || '')));
  const mandatory = new Set(_ARCHITECT_DIGEST_MANDATORY_EVENTS);
  grid.innerHTML = '';
  _ARCHITECT_DIGEST_EVENT_CATALOG.forEach((kind) => {
    const label = document.createElement('label');
    label.className = 'gs-checkbox';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.dataset.eventKind = kind;
    input.id = `gs-architect-event-${kind.replace(/_/g, '-')}`;
    input.checked = set.has(kind) || mandatory.has(kind);
    if (mandatory.has(kind)) {
      input.disabled = true;
      input.dataset.mandatory = '1';
    }
    const text = document.createElement('span');
    text.textContent = ' ' + kind + (mandatory.has(kind) ? ' (always on)' : '');
    label.appendChild(input);
    label.appendChild(text);
    grid.appendChild(label);
  });
}

function _getArchitectEnabledEvents() {
  const grid = document.getElementById('gs-architect-events-grid');
  if (!grid) return _ARCHITECT_DIGEST_DEFAULT_EVENTS.slice();
  const out = [];
  grid.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
    if (cb.checked && cb.dataset.eventKind && cb.dataset.mandatory !== '1') {
      out.push(cb.dataset.eventKind);
    }
  });
  return out;
}

function _resetGsArchitectSections() {
  _resetGsSubTabs('architect', 'architect-general');
}

function _showGroupSettings(group, data) {
  _settingsGroup = group;
  _wireGroupSettingsTooltipText();
  const s = data.settings;
  const ws = Object.assign(
    _defaultEngineerNotificationSettings(),
    data.engineer_settings || {}
  );
  const architectSettings = Object.assign(
    _defaultArchitectSettings(),
    data.architect_settings || {}
  );
  const engineer = s.engineer_agent_id && state.agents ? state.agents[s.engineer_agent_id] : null;

  document.getElementById('gs-title').textContent = group + ' Settings';

  /* -- Group tab -- */
  document.getElementById('gs-directory').value = s.default_directory || '';
  document.getElementById('gs-shell').value = s.shell || '';
  document.getElementById('gs-max-agents').value = s.max_agents || 0;
  document.getElementById('gs-collapsed').checked = s.collapsed_default || false;
  document.getElementById('gs-filter-window').checked = s.filter_by_window || false;
  document.getElementById('gs-env-vars').value = _envToText(s.env_vars);
  document.getElementById('gs-env-file').value = s.env_file || '';

  /* -- Group > Agents (all-kinds defaults) + Workers tab -- */
  document.getElementById('gs-agent-directory').value = s.agent_directory || '';
  document.getElementById('gs-agent-shell').value = s.agent_shell || '';
  _populateProviderSelect('gs-agent-provider', s.agent_provider || '', false);
  _populateTemplateSelect('gs-default-agent-template', s.default_agent_template || '', 'None');
  document.getElementById('gs-agent-boot-cmd').value = s.agent_boot_command || '';
  document.getElementById('gs-agent-model').value = s.agent_model || '';
  document.getElementById('gs-agent-reasoning-effort').value = s.agent_reasoning_effort || '';
  _populateProviderSelect('gs-worker-provider', s.worker_provider || '', true);
  document.getElementById('gs-worker-boot-command').value = s.worker_boot_command || '';
  document.getElementById('gs-worker-model').value = s.worker_model || '';
  document.getElementById('gs-worker-reasoning-effort').value = s.worker_reasoning_effort || '';
  onGsProviderChange();
  onGsWorkerProviderChange(s.worker_reasoning_effort || '');
  document.getElementById('gs-worktree').checked = s.git_worktree || false;
  document.getElementById('gs-wt-base-dir').value = s.worktree_base_dir || '.torque/worktrees';
  document.getElementById('gs-wt-base-branch').value = s.worktree_base_branch || '';
  document.getElementById('gs-wt-auto-checkpoint').checked = s.worktree_auto_checkpoint || false;
  document.getElementById('gs-wt-checkpoint-on-progress').checked = s.checkpoint_on_progress || false;
  document.getElementById('gs-wt-merge-squash').checked = s.worktree_merge_squash === true;
  _setSelectValue('gs-engineer-merge-mode', s.engineer_merge_mode, 'pr');
  document.getElementById('gs-wt-merge-instructions').value = s.worktree_merge_instructions || '';
  _setSelectValue('gs-wt-merge-cleanup', s.worktree_merge_cleanup, 'keep');
  document.getElementById('gs-wt-merge-preserve-diff').checked = !!s.worktree_merge_preserve_diff;
  document.getElementById('gs-wt-symlink-gitignored').checked = !!s.worktree_symlink_gitignored_paths;
  _gsWtSymlinks = (s.worktree_symlinks || []).slice();
  _renderWtSymlinks();
  _toggleWorktreeFields();
  document.getElementById('gs-session-resume').checked = s.agent_session_resume !== false;
  document.getElementById('gs-agent-idle-timeout').value = s.agent_idle_timeout != null ? s.agent_idle_timeout : 0;
  document.getElementById('gs-guidance-hint-cadence').value =
    s.guidance_hint_cadence != null ? s.guidance_hint_cadence : 4;
  document.getElementById('gs-notifications').checked = s.notifications || false;
  document.getElementById('gs-notify-finish').checked = s.notify_on_finish !== false;
  document.getElementById('gs-notify-error').checked = s.notify_on_error !== false;
  document.getElementById('gs-notify-attention').checked = s.notify_on_attention !== false;
  document.getElementById('gs-agent-env-vars').value = _envToText(s.agent_env_vars);
  document.getElementById('gs-agent-env-file').value = s.agent_env_file || '';

  /* -- Group > Sync provider sub-tab -- */
  const syncProvider = s.board_sync_provider || 'none';
  const syncGithub = (s.board_sync_github && typeof s.board_sync_github === 'object')
    ? s.board_sync_github
    : {};
  _setSelectValue('gs-board-sync-provider', syncProvider, 'none');
  document.getElementById('gs-board-sync-enabled').checked = !!s.board_sync_enabled;
  document.getElementById('gs-board-sync-github-repo').value = syncGithub.github_repo || '';
  document.getElementById('gs-board-sync-github-project-owner').value = syncGithub.github_project_owner || '';
  document.getElementById('gs-board-sync-github-project-number').value = syncGithub.github_project_number || '';
  document.getElementById('gs-board-sync-github-project-id').value = syncGithub.github_project_id || '';
  document.getElementById('gs-board-sync-github-status-field').value = syncGithub.github_project_status_field || 'Status';
  document.getElementById('gs-board-sync-github-lane-map').value = _gsStringifyJsonMap(syncGithub.github_lane_status_map);
  document.getElementById('gs-board-sync-github-close-via-pr').checked = syncGithub.github_close_issues_via_pr !== false;
  document.getElementById('gs-board-sync-github-create-labels').checked = syncGithub.github_create_missing_labels !== false;
  document.getElementById('gs-board-sync-github-assignee-map').value = _gsStringifyJsonMap(syncGithub.github_assignee_map);
  gsBoardSyncMapRender('lane');
  gsBoardSyncMapRender('assignee');
  _gsBoardSyncProjectOptions = [];
  _gsBoardSyncProjectsLoadedKey = '';
  _gsBoardSyncRenderProjectOptions();
  _gsBoardSyncSetProjectStatus('', '');
  _gsBoardSyncPreflightMode = '';
  _gsBoardSyncSetPreflightStatus('', '');
  onGsBoardSyncProviderChange(false);

  /* -- Engineer tab -- */
  _populateProviderSelect('gs-engineer-provider', ws.engineer_provider || '', true);
  document.getElementById('gs-engineer-boot-cmd').value = ws.engineer_boot_command || '';
  document.getElementById('gs-engineer-model').value = ws.engineer_model || '';
  document.getElementById('gs-engineer-reasoning-effort').value = ws.engineer_reasoning_effort || '';
  document.getElementById('gs-engineer-directory').value = ws.engineer_directory || '';
  document.getElementById('gs-engineer-shell').value = ws.engineer_shell || '';
  document.getElementById('gs-engineer-custom-instructions').value = ws.custom_instructions || '';
  onGsEngineerProviderChange();
  // Default specializations picker state — primed from the group setting,
  // refreshed in place when state.specializations updates over WS.
  _gsEngineerSpecs = Array.isArray(s.default_engineer_specializations)
    ? s.default_engineer_specializations.slice()
    : [];
  send({ cmd: 'list_specializations', group: group });
  renderGsEngineerSpecializations();
  _setEngineerWorkerVisibilityPermission(!!ws.restrict_to_created_agents);
  document.getElementById('gs-engineer-can-override-worker-provider').checked = ws.engineer_can_override_worker_provider !== false;
  _setSelectValue('gs-engineer-autonomy-mode', ws.autonomy_mode, 'dispatch_when_clear');
  _setSelectValue(
    'gs-engineer-default-worker-concurrency',
    ws.default_worker_concurrency,
    2
  );
  _setSelectValue(
    'gs-engineer-wave-size-preference',
    ws.wave_size_preference,
    'small'
  );
  _setSelectValue(
    'gs-engineer-same-agent-follow-up-preference',
    ws.same_agent_follow_up_preference,
    'balanced'
  );
  document.getElementById('gs-engineer-behavior-requires-user-approval').checked =
    !!s.engineer_behavior_requires_user_approval;
  _setSelectValue(
    'gs-engineer-digest-verbosity',
    ws.digest_verbosity,
    'balanced'
  );
  _setSelectValue(
    'gs-engineer-escalation-style',
    ws.escalation_style,
    'note_then_ask'
  );
  _setSelectValue('gs-engineer-push-interval', ws.push_interval, 60);
  _setSelectValue('gs-engineer-max-interval', ws.max_interval, 300);
  _setSelectValue(
    'gs-engineer-heartbeat-interval',
    ws.heartbeat_interval,
    ws.max_interval || 300
  );
  _setEngineerEventCheckboxes(ws.enabled_events || []);
  syncGsEngineerNotificationPreset();

  /* -- Architect tab -- */
  _populateProviderSelect(
    'gs-architect-provider',
    architectSettings.architect_provider || '',
    true
  );
  document.getElementById('gs-architect-boot-cmd').value = architectSettings.architect_boot_command || '';
  document.getElementById('gs-architect-model').value = architectSettings.architect_model || '';
  document.getElementById('gs-architect-reasoning-effort').value = architectSettings.architect_reasoning_effort || '';
  document.getElementById('gs-architect-directory').value = architectSettings.architect_directory || '';
  document.getElementById('gs-architect-shell').value = architectSettings.architect_shell || '';
  document.getElementById('gs-architect-custom-instructions').value = architectSettings.architect_custom_instructions || '';
  _autoGrowTextArea('gs-architect-custom-instructions');
  _setSelectValue(
    'gs-architect-autonomy-mode',
    architectSettings.architect_autonomy_mode,
    'dispatch_after_confirm'
  );
  _setSelectValue(
    'gs-architect-digest-verbosity',
    architectSettings.architect_digest_verbosity,
    'balanced'
  );
  _setSelectValue(
    'gs-architect-push-interval',
    architectSettings.architect_push_interval,
    300
  );
  _setSelectValue(
    'gs-architect-max-interval',
    architectSettings.architect_max_interval,
    600
  );
  _setSelectValue(
    'gs-architect-heartbeat-interval',
    architectSettings.architect_heartbeat_interval,
    0
  );
  document.getElementById('gs-architect-suppress-empty').checked = (
    architectSettings.architect_suppress_empty_digests !== false
  );
  const archEvents = (
    Array.isArray(architectSettings.architect_enabled_events)
    && architectSettings.architect_enabled_events.length
  )
    ? architectSettings.architect_enabled_events
    : _ARCHITECT_DIGEST_DEFAULT_EVENTS.slice();
  _renderArchitectEventCheckboxes(archEvents);
  _populateArchitectJournalCheckpointSelect(
    architectSettings.architect_journal_checkpoint_frequency || 'every_10_actions'
  );
  onGsArchitectProviderChange();

  const selection = _normalizeGsSelection(_gsInitialTab || 'group', _gsInitialSubtab || '');
  const initialTab = selection.tab;
  const initialSubtab = selection.subtab;
  switchGsTab(initialTab);
  if (initialSubtab) {
    const btn = document.querySelector(`.gs-pane[data-pane="${initialTab}"] .gs-subtab[data-subtab="${initialSubtab}"]`);
    if (btn) switchGsSubTab(initialTab, btn);
  }
  _gsInitialTab = 'group';
  _gsInitialSubtab = '';
  document.getElementById('modal-group-settings').classList.add('visible');
  if (typeof settingsShellCaptureBaseline === 'function') {
    settingsShellCaptureBaseline('modal-group-settings');
  }
  const focusId = initialTab === 'engineer'
    ? 'gs-engineer-provider'
    : initialTab === 'architect'
      ? 'gs-architect-provider'
      : initialTab === 'workers'
        ? 'gs-agent-directory'
        : initialSubtab === 'group-worker-defaults'
          ? 'gs-agent-provider'
      : initialSubtab === 'group-sync'
          ? 'gs-board-sync-provider'
        : initialSubtab === 'group-advanced'
          ? 'gs-guidance-hint-cadence'
        : 'gs-directory';
  const focusEl = document.getElementById(focusId);
  if (focusEl) focusEl.focus();
  if (initialTab === 'group' && initialSubtab === 'group-sync') {
    _gsBoardSyncMaybeLoadProjects();
  }
}

function submitGroupSettings() {
  if (!_settingsGroup) return;
  const boardSyncLaneMap = _gsParseJsonMap(
    'gs-board-sync-github-lane-map',
    'Lane → status mapping'
  );
  if (boardSyncLaneMap === null) return;
  const boardSyncAssigneeMap = _gsParseJsonMap(
    'gs-board-sync-github-assignee-map',
    'Assignee map'
  );
  if (boardSyncAssigneeMap === null) return;
  const boardSyncProjectNumber = parseInt(
    document.getElementById('gs-board-sync-github-project-number').value,
    10
  ) || 0;
  const guidanceHintCadence = parseInt(
    document.getElementById('gs-guidance-hint-cadence').value,
    10
  );

  const settings = {
    /* Group */
    default_directory: document.getElementById('gs-directory').value.trim(),
    shell: document.getElementById('gs-shell').value,
    env_vars: _textToEnv('gs-env-vars'),
    env_file: document.getElementById('gs-env-file').value.trim(),
    max_agents: parseInt(document.getElementById('gs-max-agents').value) || 0,
    collapsed_default: document.getElementById('gs-collapsed').checked,
    filter_by_window: document.getElementById('gs-filter-window').checked,
    /* Group-wide worker defaults + worker execution */
    agent_directory: document.getElementById('gs-agent-directory').value.trim(),
    agent_shell: document.getElementById('gs-agent-shell').value,
    default_agent_template: document.getElementById('gs-default-agent-template').value,
    agent_provider: _getProviderValue('gs-agent-provider'),
    agent_boot_command: document.getElementById('gs-agent-boot-cmd').value.trim(),
    agent_model: document.getElementById('gs-agent-model').value.trim(),
    agent_reasoning_effort: document.getElementById('gs-agent-reasoning-effort').value,
    worker_provider: _getProviderValue('gs-worker-provider'),
    worker_boot_command: document.getElementById('gs-worker-boot-command').value.trim(),
    worker_model: document.getElementById('gs-worker-model').value.trim(),
    worker_reasoning_effort: document.getElementById('gs-worker-reasoning-effort').value,
    agent_env_vars: _textToEnv('gs-agent-env-vars'),
    agent_env_file: document.getElementById('gs-agent-env-file').value.trim(),
    git_worktree: document.getElementById('gs-worktree').checked,
    worktree_base_dir: document.getElementById('gs-wt-base-dir').value.trim() || '.torque/worktrees',
    worktree_base_branch: document.getElementById('gs-wt-base-branch').value.trim(),
    worktree_auto_checkpoint: document.getElementById('gs-wt-auto-checkpoint').checked,
    checkpoint_on_progress: document.getElementById('gs-wt-checkpoint-on-progress').checked,
    worktree_merge_squash: document.getElementById('gs-wt-merge-squash').checked,
    engineer_merge_mode: document.getElementById('gs-engineer-merge-mode').value,
    worktree_merge_instructions: document.getElementById('gs-wt-merge-instructions').value.trim(),
    worktree_merge_cleanup: document.getElementById('gs-wt-merge-cleanup').value,
    worktree_merge_preserve_diff: document.getElementById('gs-wt-merge-preserve-diff').checked,
    worktree_symlink_gitignored_paths: document.getElementById('gs-wt-symlink-gitignored').checked,
    worktree_symlinks: _gsWtSymlinks.slice(),
    agent_session_resume: document.getElementById('gs-session-resume').checked,
    agent_idle_timeout: parseInt(document.getElementById('gs-agent-idle-timeout').value) || 0,
    guidance_hint_cadence: Number.isNaN(guidanceHintCadence) ? 4 : guidanceHintCadence,
    engineer_behavior_requires_user_approval: document.getElementById('gs-engineer-behavior-requires-user-approval').checked,
    notifications: document.getElementById('gs-notifications').checked,
    notify_on_finish: document.getElementById('gs-notify-finish').checked,
    notify_on_error: document.getElementById('gs-notify-error').checked,
    notify_on_attention: document.getElementById('gs-notify-attention').checked,
    /* Board sync */
    board_sync_provider: document.getElementById('gs-board-sync-provider').value || 'none',
    board_sync_enabled: document.getElementById('gs-board-sync-enabled').checked,
    board_sync_github: {
      github_repo: document.getElementById('gs-board-sync-github-repo').value.trim(),
      github_project_owner: document.getElementById('gs-board-sync-github-project-owner').value.trim(),
      github_project_number: boardSyncProjectNumber,
      github_project_id: document.getElementById('gs-board-sync-github-project-id').value.trim(),
      github_project_status_field: document.getElementById('gs-board-sync-github-status-field').value.trim() || 'Status',
      github_lane_status_map: boardSyncLaneMap,
      github_close_issues_via_pr: document.getElementById('gs-board-sync-github-close-via-pr').checked,
      github_create_missing_labels: document.getElementById('gs-board-sync-github-create-labels').checked,
      github_assignee_map: boardSyncAssigneeMap,
    },
    default_engineer_specializations: (_gsEngineerSpecs || []).slice(),
  };
  const engineerSettings = {
    engineer_provider: _getProviderValue('gs-engineer-provider'),
    engineer_boot_command: document.getElementById('gs-engineer-boot-cmd').value.trim(),
    engineer_model: document.getElementById('gs-engineer-model').value.trim(),
    engineer_reasoning_effort: document.getElementById('gs-engineer-reasoning-effort').value,
    engineer_directory: document.getElementById('gs-engineer-directory').value.trim(),
    engineer_shell: document.getElementById('gs-engineer-shell').value,
    custom_instructions: document.getElementById('gs-engineer-custom-instructions').value,
    restrict_to_created_agents: _getEngineerRestrictToCreatedAgentsFromPermission(),
    engineer_can_override_worker_provider: document.getElementById('gs-engineer-can-override-worker-provider').checked,
    autonomy_mode: document.getElementById('gs-engineer-autonomy-mode').value,
    default_worker_concurrency: parseInt(document.getElementById('gs-engineer-default-worker-concurrency').value, 10) || 2,
    wave_size_preference: document.getElementById('gs-engineer-wave-size-preference').value,
    same_agent_follow_up_preference: document.getElementById('gs-engineer-same-agent-follow-up-preference').value,
    digest_verbosity: document.getElementById('gs-engineer-digest-verbosity').value,
    escalation_style: document.getElementById('gs-engineer-escalation-style').value,
    push_interval: parseInt(document.getElementById('gs-engineer-push-interval').value, 10) || 60,
    max_interval: parseInt(document.getElementById('gs-engineer-max-interval').value, 10) || 300,
    heartbeat_interval: parseInt(document.getElementById('gs-engineer-heartbeat-interval').value, 10),
    enabled_events: _getEngineerEnabledEvents(),
  };
  const architectSettings = {
    architect_provider: _getProviderValue('gs-architect-provider'),
    architect_boot_command: document.getElementById('gs-architect-boot-cmd').value.trim(),
    architect_model: document.getElementById('gs-architect-model').value.trim(),
    architect_reasoning_effort: document.getElementById('gs-architect-reasoning-effort').value,
    architect_directory: document.getElementById('gs-architect-directory').value.trim(),
    architect_shell: document.getElementById('gs-architect-shell').value,
    architect_custom_instructions: document.getElementById('gs-architect-custom-instructions').value,
    architect_autonomy_mode: document.getElementById('gs-architect-autonomy-mode').value,
    architect_digest_verbosity: document.getElementById('gs-architect-digest-verbosity').value,
    architect_push_interval: parseInt(document.getElementById('gs-architect-push-interval').value, 10) || 300,
    architect_max_interval: parseInt(document.getElementById('gs-architect-max-interval').value, 10) || 600,
    architect_heartbeat_interval: parseInt(document.getElementById('gs-architect-heartbeat-interval').value, 10) || 0,
    architect_suppress_empty_digests: document.getElementById('gs-architect-suppress-empty').checked,
    architect_enabled_events: _getArchitectEnabledEvents(),
    architect_journal_checkpoint_frequency: document.getElementById('gs-architect-journal-checkpoint').value.trim() || 'every_10_actions',
  };

  send({ cmd: 'update_group_settings', group: _settingsGroup, settings });
  send({ cmd: 'engineer_update_settings', group: _settingsGroup, ...engineerSettings });
  send({ cmd: 'update_architect_settings', group: _settingsGroup, settings: architectSettings });
  _settingsGroup = null;
  closeModals();
}

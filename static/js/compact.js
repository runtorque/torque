/* Compact-snapshot consumer (compact-v1).
 *
 * Opt-in WebSocket protocol that sends a lean initial snapshot and lazy-loads
 * heavy task/decision/hire/archive/weaver detail on demand. See
 * docs/compact-snapshot-v1.md for the wire contract.
 *
 * Safe rollout: localStorage flag "loom:snapshot_protocol" = "compact-v1"
 * (or "1"/"compact") appends ?compact=1 to the WS URL. Default off.
 */

const COMPACT_SNAPSHOT_PROTOCOL = 'compact-v1';
const COMPACT_FLAG_STORAGE_KEY = 'loom:snapshot_protocol';

/* Fields the compact board_tasks entry does NOT carry. If a local task is
 * missing these we must lazy-load the full detail before rendering modals
 * or history panels. Mirrors docs/compact-snapshot-v1.md §board_tasks. */
const COMPACT_HEAVY_TASK_FIELDS = [
  'description',
  'messages',
  'artifacts',
  'attachments',
  'instructions',
  'context',
  'criteria',
  'action_vars',
  'health_details',
  'verification_summary',
  'verification_notes',
  'worktree_boundary',
];

function _compactFlagValue() {
  try {
    if (typeof localStorage === 'undefined') return '';
    var v = localStorage.getItem(COMPACT_FLAG_STORAGE_KEY);
    return v == null ? '' : String(v).trim().toLowerCase();
  } catch (_err) {
    return '';
  }
}

function _compactFlagEnabled() {
  var v = _compactFlagValue();
  return v === '1' || v === 'true' || v === 'yes'
    || v === 'compact' || v === COMPACT_SNAPSHOT_PROTOCOL;
}

function _compactPrepareWSUrl(url) {
  if (!_compactFlagEnabled()) return url;
  var sep = url.indexOf('?') >= 0 ? '&' : '?';
  return url + sep + 'compact=1';
}

function _compactModeActive() {
  return !!(state
    && String(state.snapshot_protocol || '') === COMPACT_SNAPSHOT_PROTOCOL);
}

/* Called after _handleFullState assigns the new snapshot onto `state`. In
 * compact mode the backend omits decisions/pending_hires/archived/weaver
 * detail entirely; initialize them to empty maps so render code can treat
 * "missing" the same as "empty" without null-guarding everywhere. */
function _compactInitDeferredMaps() {
  if (!_compactModeActive()) return;
  if (!state.decisions) state.decisions = {};
  if (!state.pending_hires) state.pending_hires = {};
  if (!state.board_tasks_archived) state.board_tasks_archived = {};
  if (!state.weaver_journal) state.weaver_journal = {};
  if (!state.weaver_worklog) state.weaver_worklog = {};
  if (!state.weaver_streams) state.weaver_streams = {};
  // Reset dedup/fetched bookkeeping so a resync can re-hydrate cleanly.
  _compactInFlight = {};
  _compactArchivedFetchedGroups = {};
  _compactWeaverFetchedGroups = {};
  _compactTasksFullyLoaded = {};
  _compactDecisionsFetched = false;
  _compactPendingHiresFetched = false;
}

/* Dedup registry keyed by request signature. */
var _compactInFlight = {};
var _compactPendingTaskDetailCbs = {};
var _compactArchivedFetchedGroups = {};
var _compactWeaverFetchedGroups = {};
var _compactTasksFullyLoaded = {};
var _compactDecisionsFetched = false;
var _compactPendingHiresFetched = false;

function _compactSend(obj) {
  if (typeof send === 'function') send(obj);
}

function lazyLoadDecisions(opts) {
  if (!_compactModeActive()) return false;
  var key = 'decisions:' + ((opts && opts.include_archived) ? '1' : '0');
  if (_compactInFlight[key]) return false;
  _compactInFlight[key] = true;
  var payload = { cmd: 'decisions_snapshot' };
  if (opts && opts.include_archived) payload.include_archived = true;
  _compactSend(payload);
  return true;
}

function lazyLoadPendingHires(opts) {
  if (!_compactModeActive()) return false;
  var filter = (opts && opts.status_filter) || 'pending';
  var architectId = (opts && opts.architect_id) || '';
  var key = 'pending_hires:' + filter + ':' + architectId;
  if (_compactInFlight[key]) return false;
  _compactInFlight[key] = true;
  var payload = { cmd: 'pending_hires_snapshot', status_filter: filter };
  if (architectId) payload.architect_id = architectId;
  _compactSend(payload);
  return true;
}

function lazyLoadArchivedTasks(group) {
  if (!_compactModeActive()) return false;
  var g = String(group || '');
  if (_compactArchivedFetchedGroups[g]) return false;
  var key = 'archived:' + g;
  if (_compactInFlight[key]) return false;
  _compactInFlight[key] = true;
  var payload = { cmd: 'archived_tasks' };
  if (g) payload.group = g;
  _compactSend(payload);
  return true;
}

function lazyLoadWeaverJournal(group, opts) {
  if (!_compactModeActive()) return false;
  var g = String(group || '');
  if (!g) return false;
  if (_compactWeaverFetchedGroups[g]) return false;
  var key = 'weaver_journal:' + g;
  if (_compactInFlight[key]) return false;
  _compactInFlight[key] = true;
  var payload = { cmd: 'weaver_journal_snapshot', group: g };
  if (opts && typeof opts.limit === 'number') payload.limit = opts.limit;
  if (opts && typeof opts.worklog_limit === 'number') {
    payload.worklog_limit = opts.worklog_limit;
  }
  if (opts && opts.include_streams === false) payload.include_streams = false;
  _compactSend(payload);
  return true;
}

function _compactTaskHasFullDetail(task) {
  if (!task) return false;
  if (_compactTasksFullyLoaded[task.id]) return true;
  for (var i = 0; i < COMPACT_HEAVY_TASK_FIELDS.length; i++) {
    if (Object.prototype.hasOwnProperty.call(task, COMPACT_HEAVY_TASK_FIELDS[i])) {
      return true;
    }
  }
  return false;
}

function lazyLoadTaskDetail(taskId, cb) {
  if (!taskId) return false;
  if (!_compactModeActive()) {
    if (typeof cb === 'function') {
      var local = (state && state.board_tasks) ? state.board_tasks[taskId] : null;
      cb(local || null);
    }
    return false;
  }
  var cbs = _compactPendingTaskDetailCbs[taskId] || [];
  if (typeof cb === 'function') cbs.push(cb);
  _compactPendingTaskDetailCbs[taskId] = cbs;
  var key = 'task_detail:' + taskId;
  if (_compactInFlight[key]) return true;
  _compactInFlight[key] = true;
  _compactSend({ cmd: 'task_detail', id: taskId });
  return true;
}

function ensureTaskDetail(taskId, cb) {
  var task = (state && state.board_tasks) ? state.board_tasks[taskId] : null;
  if (!_compactModeActive() || _compactTaskHasFullDetail(task)) {
    if (typeof cb === 'function') cb(task || null);
    return false;
  }
  return lazyLoadTaskDetail(taskId, cb);
}

function _compactClearInFlight(key) {
  if (key in _compactInFlight) delete _compactInFlight[key];
}

function _compactApplyTaskDetail(msg) {
  var id = msg && msg.id ? String(msg.id) : '';
  if (!id) return;
  _compactClearInFlight('task_detail:' + id);
  var full = (msg && msg.task) ? msg.task : null;
  if (!state.board_tasks) state.board_tasks = {};
  if (full) {
    var existing = state.board_tasks[id];
    state.board_tasks[id] = existing
      ? Object.assign({}, existing, full)
      : Object.assign({}, full);
    _compactTasksFullyLoaded[id] = true;
    if (typeof _invalidateTaskLookupIndex === 'function') {
      _invalidateTaskLookupIndex();
    }
  }
  var cbs = _compactPendingTaskDetailCbs[id] || [];
  delete _compactPendingTaskDetailCbs[id];
  for (var i = 0; i < cbs.length; i++) {
    try {
      cbs[i](full);
    } catch (err) {
      if (typeof console !== 'undefined' && console.warn) {
        console.warn('task_detail callback failed', err);
      }
    }
  }
}

function _compactApplyDecisionsSnapshot(msg) {
  _compactClearInFlight(
    'decisions:' + ((msg && msg.include_archived) ? '1' : '0'));
  // Some backends may not echo include_archived back; clear both.
  _compactClearInFlight('decisions:1');
  _compactClearInFlight('decisions:0');
  if (!state.decisions) state.decisions = {};
  var merged = (msg && msg.decisions) || {};
  for (var did in merged) state.decisions[did] = merged[did];
  _compactDecisionsFetched = true;
  if (typeof _agentPanelInvalidateArchitectDecisionCache === 'function') {
    _agentPanelInvalidateArchitectDecisionCache();
  }
}

function _compactApplyPendingHiresSnapshot(msg) {
  // Clear any filter/architect permutation in flight.
  for (var k in _compactInFlight) {
    if (k.indexOf('pending_hires:') === 0) delete _compactInFlight[k];
  }
  if (!state.pending_hires) state.pending_hires = {};
  var merged = (msg && msg.pending_hires) || {};
  for (var hid in merged) state.pending_hires[hid] = merged[hid];
  _compactPendingHiresFetched = true;
}

function _compactApplyArchivedTasks(msg) {
  var g = msg && msg.group ? String(msg.group) : '';
  _compactClearInFlight('archived:' + g);
  _compactArchivedFetchedGroups[g] = true;
  if (!state.board_tasks) state.board_tasks = {};
  var merged = (msg && msg.board_tasks) || {};
  for (var tid in merged) {
    state.board_tasks[tid] = Object.assign(
      {}, state.board_tasks[tid] || {}, merged[tid]);
    _compactTasksFullyLoaded[tid] = true;
  }
  if (typeof _invalidateTaskLookupIndex === 'function') {
    _invalidateTaskLookupIndex();
  }
}

function _compactApplyWeaverJournalSnapshot(msg) {
  var g = msg && msg.group ? String(msg.group) : '';
  if (!g) return;
  _compactClearInFlight('weaver_journal:' + g);
  _compactWeaverFetchedGroups[g] = true;
  if (!state.weaver_journal) state.weaver_journal = {};
  if (!state.weaver_worklog) state.weaver_worklog = {};
  if (!state.weaver_streams) state.weaver_streams = {};
  var journal = (msg.weaver_journal && msg.weaver_journal[g]) || [];
  var worklog = (msg.weaver_worklog && msg.weaver_worklog[g]) || [];
  var streams = msg.weaver_streams && msg.weaver_streams[g];
  state.weaver_journal[g] = journal.slice();
  state.weaver_worklog[g] = worklog.slice();
  if (streams !== undefined) state.weaver_streams[g] = streams;
}

/* Route one of the lazy-load response frames. Returns true if the message
 * was handled here. ws.js calls this from its `onmessage` dispatcher before
 * falling through to existing handlers. */
function _compactHandleLazyResponse(msg) {
  if (!msg || !msg.type) return false;
  switch (msg.type) {
    case 'task_detail':
      _compactApplyTaskDetail(msg);
      return true;
    case 'decisions_snapshot':
      _compactApplyDecisionsSnapshot(msg);
      return true;
    case 'pending_hires_snapshot':
      _compactApplyPendingHiresSnapshot(msg);
      return true;
    case 'archived_tasks':
      _compactApplyArchivedTasks(msg);
      return true;
    case 'weaver_journal_snapshot':
      _compactApplyWeaverJournalSnapshot(msg);
      return true;
    default:
      return false;
  }
}

/* Auto-hydrate deferred maps on first compact state. Decisions and pending
 * hires are small (<1KB typically) and the architect/agent panels need them
 * eagerly; skipping them is the documented migration path (step 2), but in
 * this first consumer cut we fetch once per connect so the agent panel
 * behaves identically to the legacy full-state client. */
function _compactAutoHydrateOnConnect() {
  if (!_compactModeActive()) return;
  if (!_compactDecisionsFetched) lazyLoadDecisions();
  if (!_compactPendingHiresFetched) lazyLoadPendingHires();
}

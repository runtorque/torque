/* Compact-snapshot consumer (compact-v1).
 *
 * Default-on WebSocket protocol that sends a lean initial snapshot and
 * lazy-loads heavy task/decision/hire/archive/engineer detail on demand.
 * See docs/compact-snapshot-v1.md for the wire contract.
 *
 * Rollback hatch: localStorage flag "torque:snapshot_protocol" = "legacy"
 * (or "off" / "0" / "false") drops the client back onto the legacy full
 * snapshot for the session. Any other value (including unset) opts into
 * compact-v1.
 */

const COMPACT_SNAPSHOT_PROTOCOL = 'compact-v1';
const COMPACT_FLAG_STORAGE_KEY = 'torque:snapshot_protocol';

/* Fields the compact board_tasks entry still defers entirely. If a local task
 * needs these we must lazy-load the full detail before rendering modals or
 * history panels. Mirrors docs/compact-snapshot-v1.md §board_tasks. */
const COMPACT_HEAVY_TASK_FIELDS = [
  'description',
  'artifacts',
  'attachments',
  'instructions',
  'context',
  'criteria',
  'action_vars',
  'agent_template',
  'messages_thread',
  'verification_notes',
  'verification_summary',
  'completion_evidence',
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
  // Default-on: compact is the normal path. Operators who need the legacy
  // full-snapshot shape can set localStorage["torque:snapshot_protocol"] to
  // one of the opt-out sentinels below.
  var v = _compactFlagValue();
  return v !== 'legacy' && v !== 'off' && v !== '0' && v !== 'false';
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
 * compact mode the backend omits decisions/pending_hires/archived/engineer
 * detail entirely; initialize them to empty maps so render code can treat
 * "missing" the same as "empty" without null-guarding everywhere. */
function _compactInitDeferredMaps() {
  if (!_compactModeActive()) return;
  if (!state.decisions) state.decisions = {};
  if (!state.pending_hires) state.pending_hires = {};
  if (!state.initiatives) state.initiatives = {};
  if (!state.areas) state.areas = {};
  if (!state.thinking) state.thinking = { scratchpad_notes: {} };
  if (!state.thinking.scratchpad_notes) state.thinking.scratchpad_notes = {};
  if (!state.idea_briefs) state.idea_briefs = {};
  if (!state.board_tasks_archived) state.board_tasks_archived = {};
  if (!state.engineer_journal) state.engineer_journal = {};
  if (!state.engineer_worklog) state.engineer_worklog = {};
  if (!state.engineer_streams) state.engineer_streams = {};
  // Reset dedup/fetched bookkeeping so a resync can re-hydrate cleanly.
  _compactInFlight = {};
  _compactArchivedFetchedGroups = {};
  _compactEngineerFetchedGroups = {};
  _compactTasksFullyLoaded = {};
  _compactDecisionsFetched = false;
  _compactDecisionsFetchedWithArchived = false;
  _compactPendingHiresFetched = false;
}

/* Dedup registry keyed by request signature. */
var _compactInFlight = {};
var _compactPendingTaskDetailCbs = {};
var _compactArchivedFetchedGroups = {};
var _compactEngineerFetchedGroups = {};
var _compactTasksFullyLoaded = {};
var _compactDecisionsFetched = false;
var _compactDecisionsFetchedWithArchived = false;
var _compactPendingHiresFetched = false;

function _compactSend(obj) {
  if (typeof send === 'function') send(obj);
}

function lazyLoadDecisions(opts) {
  if (!_compactModeActive()) return false;
  var includeArchived = !opts || typeof opts.include_archived === 'undefined'
    ? true
    : !!opts.include_archived;
  if (includeArchived && _compactDecisionsFetchedWithArchived) return false;
  if (!includeArchived && (_compactDecisionsFetched || _compactDecisionsFetchedWithArchived)) return false;
  var key = 'decisions:' + (includeArchived ? '1' : '0');
  if (_compactInFlight[key]) return false;
  _compactInFlight[key] = true;
  var payload = { cmd: 'decisions_snapshot' };
  if (includeArchived) payload.include_archived = true;
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

function lazyLoadEngineerJournal(group, opts) {
  if (!_compactModeActive()) return false;
  var g = String(group || '');
  if (!g) return false;
  if (_compactEngineerFetchedGroups[g]) return false;
  var key = 'engineer_journal:' + g;
  if (_compactInFlight[key]) return false;
  _compactInFlight[key] = true;
  var payload = { cmd: 'engineer_journal_snapshot', group: g };
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
  // A delta might have enriched a single heavy field (e.g. a task_upsert
  // carrying only action_vars) without actually delivering the rest. Only
  // the fully-loaded registry — populated by an authoritative task_detail
  // or archived_tasks merge — may short-circuit a fresh fetch in compact
  // mode. Falling through means one extra round-trip in the rare
  // partially-enriched case, but guarantees no consumer sees a stale card.
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

/* Read-time hydrator for panels that iterate state.board_tasks and need
 * heavy fields on every row they visit (e.g. the events attention feed
 * walking all open asks, or the agent detail panel walking every task
 * attached to one agent). Callers pass a predicate that matches the
 * specific rows they read; rows missing full detail kick off a fetch.
 * The next render (driven by the task_detail merge) paints the hydrated
 * data. Bounded by ensureTaskDetail's per-task dedup, so repeated calls
 * per render are free. */
function _compactTaskThreadSummary(task) {
  var summary = task && task.messages_thread_summary;
  return (summary && typeof summary === 'object') ? summary : {};
}

function _compactTaskThreadMayTargetAgent(task, agentId) {
  agentId = String(agentId || '');
  if (!task || !agentId) return false;
  var summary = _compactTaskThreadSummary(task);
  if (!summary.count) return false;
  if (String(task.agent_id || '') === agentId) return true;
  var recipients = Array.isArray(summary.recipient_agent_ids)
    ? summary.recipient_agent_ids : [];
  for (var i = 0; i < recipients.length; i++) {
    if (String(recipients[i] || '') === agentId) return true;
  }
  return false;
}

function _compactHydrateTasksMatching(predicate) {
  if (!_compactModeActive() || typeof predicate !== 'function') return 0;
  if (!state || !state.board_tasks) return 0;
  var fired = 0;
  for (var id in state.board_tasks) {
    var task = state.board_tasks[id];
    if (!task) continue;
    try {
      if (!predicate(task)) continue;
    } catch (_err) {
      continue;
    }
    if (_compactTaskHasFullDetail(task)) continue;
    if (lazyLoadTaskDetail(id)) fired++;
  }
  return fired;
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
    if (typeof _torqueBumpStateRevision === 'function') {
      _torqueBumpStateRevision();
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
  if (msg && msg.include_archived) _compactDecisionsFetchedWithArchived = true;
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
  if (!state.initiatives) state.initiatives = {};
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
  if (typeof _torqueBumpStateRevision === 'function') {
    _torqueBumpStateRevision();
  }
}

function _compactApplyEngineerJournalSnapshot(msg) {
  var g = msg && msg.group ? String(msg.group) : '';
  if (!g) return;
  _compactClearInFlight('engineer_journal:' + g);
  _compactEngineerFetchedGroups[g] = true;
  if (!state.engineer_journal) state.engineer_journal = {};
  if (!state.engineer_worklog) state.engineer_worklog = {};
  if (!state.engineer_streams) state.engineer_streams = {};
  // engineer_journal is keyed by author_cell_id; worklog/streams remain
  // keyed by group for their group-wide surfaces.
  var journalByAuthor = (msg && msg.engineer_journal) || {};
  var worklog = (msg.engineer_worklog && msg.engineer_worklog[g]) || [];
  var streams = msg.engineer_streams && msg.engineer_streams[g];
  for (var authorId in journalByAuthor) {
    state.engineer_journal[authorId] = (journalByAuthor[authorId] || []).slice();
  }
  state.engineer_worklog[g] = worklog.slice();
  if (streams !== undefined) state.engineer_streams[g] = streams;
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
    case 'engineer_journal_snapshot':
      _compactApplyEngineerJournalSnapshot(msg);
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
  if (!_compactDecisionsFetchedWithArchived) lazyLoadDecisions({ include_archived: true });
  if (!_compactPendingHiresFetched) lazyLoadPendingHires();
}

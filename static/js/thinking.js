/* ------------------------------------------------------------------ */
/* Thinking panel — Scratchpad and Idea Briefs                            */
/* ------------------------------------------------------------------ */

var THINKING_TABS = ['scratchpad', 'idea-briefs'];
var THINKING_NEW_NOTE_ID = '__new_note__';
var THINKING_NEW_IDEA_BRIEF_ID = '__new_idea_brief__';
var IDEA_BRIEF_FIELDS = [
  'title',
  'problem_opportunity',
  'why_it_matters',
  'proposed_shape',
  'smallest_useful_version',
  'risks_tradeoffs',
  'open_questions',
];
var IDEA_BRIEF_BODY_FIELDS = IDEA_BRIEF_FIELDS.slice(1);
var IDEA_BRIEF_STATUS_ORDER = ['draft', 'proposed', 'parked', 'archived'];

var _thinkingStateRef = null;
var _thinkingActiveTab = 'scratchpad';
var _thinkingScratchLoadedGroup = null;
var _thinkingScratchLoadingGroup = null;
var _thinkingScratchSelectedId = '';
var _thinkingScratchDraftsById = {};
var _thinkingNodeEditDraftsById = {};
var _thinkingLinkEditDraftsById = {};
var _thinkingSaving = false;
var _thinkingLastError = '';
var _thinkingLastCommandAt = 0;
var _ideaBriefLoadedGroup = null;
var _ideaBriefLoadingGroup = null;
var _ideaBriefIncludeArchived = false;
var _ideaBriefSelectedId = '';
var _ideaBriefShowLoadingId = '';
// List responses deliberately contain only summary metadata. Keep that
// transport distinction outside of the persisted row so a selected summary
// cannot be rendered or saved as though it were a complete brief.
var _ideaBriefSummaryIdsById = {};
var _ideaBriefPendingDetailSurfaceState = null;
var _ideaBriefDraftsById = {};
var _ideaBriefDirtyDraftsById = {};
var _ideaBriefRenderedDraftsById = {};
var _ideaBriefSelectedLinkKey = '';
var _ideaBriefSaving = false;
var _ideaBriefLastError = '';
var _ideaBriefLastStatus = '';
var _ideaBriefLastCommandAt = 0;
var _ideaBriefProposalResultById = {};

function _thinkingEsc(value) {
  if (typeof esc === 'function') return esc(value == null ? '' : value);
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _thinkingAttr(value) {
  return _thinkingEsc(value);
}

function _thinkingJs(value) {
  return _thinkingEsc(String(value == null ? '' : value).replace(/\\/g, '\\\\'));
}

function _thinkingNowMs() {
  if (typeof Date !== 'undefined' && Date.now) return Date.now();
  return 0;
}


function _thinkingSyncStateReference() {
  if (typeof state === 'undefined' || !state) return;
  if (_thinkingStateRef === state) return;
  _thinkingStateRef = state;
  _thinkingScratchLoadedGroup = null;
  _thinkingScratchLoadingGroup = null;
  _ideaBriefLoadedGroup = null;
  _ideaBriefLoadingGroup = null;
  _ideaBriefShowLoadingId = '';
  _ideaBriefSummaryIdsById = {};
  _ideaBriefPendingDetailSurfaceState = null;
}

function _thinkingEnsureState() {
  _thinkingSyncStateReference();
  if (typeof state === 'undefined' || !state) {
    if (typeof globalThis !== 'undefined') globalThis.state = {};
  }
  if (!state.thinking || typeof state.thinking !== 'object') {
    state.thinking = { scratchpad_notes: {} };
  }
  if (!state.thinking.scratchpad_notes || typeof state.thinking.scratchpad_notes !== 'object') {
    state.thinking.scratchpad_notes = {};
  }
}

function _ideaBriefEnsureState() {
  _thinkingSyncStateReference();
  if (typeof state === 'undefined' || !state) {
    if (typeof globalThis !== 'undefined') globalThis.state = {};
  }
  if (!state.idea_briefs || typeof state.idea_briefs !== 'object') state.idea_briefs = {};
  return state.idea_briefs;
}

function _thinkingGroup() {
  _thinkingSyncStateReference();
  if (typeof _currentGroup === 'function') return _currentGroup() || '';
  return (typeof state !== 'undefined' && state && state.active_group) || '';
}

function _thinkingPanelVisible() {
  return !!(
    (typeof _panelAppVisible === 'function' && _panelAppVisible('thinking'))
    || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'thinking')
  );
}

function _thinkingActorPayload() {
  return { actor_kind: 'user', actor_id: '' };
}

function _thinkingSend(payload) {
  var data = Object.assign({}, payload || {}, _thinkingActorPayload());
  _thinkingSaving = true;
  _thinkingLastError = '';
  _thinkingLastCommandAt = _thinkingNowMs();
  if (typeof send === 'function') send(data);
}

function _thinkingItemGroup(item) {
  return String((item && (item.group || item.group_name)) || '');
}

function _thinkingIsActive(item) {
  return !!item && !item.archived && !item.deleted
    && !String(item.archived_at || '').trim()
    && !String(item.deleted_at || '').trim();
}

function _thinkingTimestamp(item) {
  var value = String((item && (item.updated_at || item.created_at)) || '');
  var parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function _thinkingTimeLabel(item) {
  var value = String((item && (item.updated_at || item.created_at)) || '');
  if (!value) return '';
  var parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  try {
    return new Date(parsed).toLocaleString([], {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    });
  } catch (_err) {
    return value;
  }
}

function _thinkingNotesForGroup(group) {
  _thinkingEnsureState();
  var items = [];
  var g = String(group || '');
  var notes = state.thinking.scratchpad_notes || {};
  for (var id in notes) {
    var item = notes[id];
    if (!item || !item.id) continue;
    if (g && _thinkingItemGroup(item) !== g) continue;
    if (!_thinkingIsActive(item)) continue;
    items.push(item);
  }
  items.sort(function(a, b) {
    var at = _thinkingTimestamp(a);
    var bt = _thinkingTimestamp(b);
    if (at !== bt) return bt - at;
    return String(a.title || a.id || '').localeCompare(String(b.title || b.id || ''));
  });
  return items;
}


function thinkingEnsureScratchpadLoaded(opts) {
  opts = opts || {};
  var group = _thinkingGroup();
  if (!group && !opts.force) return false;
  if (!opts.force && (_thinkingScratchLoadedGroup === group || _thinkingScratchLoadingGroup === group)) return false;
  _thinkingScratchLoadingGroup = group;
  _thinkingLastError = '';
  if (typeof send === 'function') send({ cmd: 'scratchpad_note_list', group: group, include_archived: false, include_deleted: false });
  return true;
}


function thinkingEnsureLoaded(opts) {
  opts = opts || {};
  if (_thinkingActiveTab === 'idea-briefs') {
    return ideaBriefEnsureLoaded(opts) || thinkingEnsureScratchpadLoaded(opts);
  }
  return thinkingEnsureScratchpadLoaded(opts);
}
function thinkingRefresh() {
  _thinkingCaptureDrafts();
  thinkingEnsureScratchpadLoaded({ force: true });
  ideaBriefEnsureLoaded({ force: true });
  if (typeof renderThinkingPanel === 'function') renderThinkingPanel();
}
function thinkingBeginGroupSwitch() {
  _thinkingCaptureDrafts();
  var group = _thinkingGroup() || '';
  var note = _thinkingScratchSelectedId && state && state.thinking && state.thinking.scratchpad_notes
    ? state.thinking.scratchpad_notes[_thinkingScratchSelectedId] : null;
  if (_thinkingScratchSelectedId !== THINKING_NEW_NOTE_ID && note && _thinkingItemGroup(note) !== group) _thinkingScratchSelectedId = '';
  var brief = _ideaBriefSelectedId && state && state.idea_briefs ? state.idea_briefs[_ideaBriefSelectedId] : null;
  if (_ideaBriefSelectedId !== THINKING_NEW_IDEA_BRIEF_ID && brief && _ideaBriefItemGroup(brief) !== group) {
    _ideaBriefSelectedId = '';
    _ideaBriefSelectedLinkKey = '';
  }
  thinkingEnsureScratchpadLoaded({ force: true });
  ideaBriefEnsureLoaded({ force: true });
  renderThinkingPanel();
}
function _thinkingFirstActiveNoteId(group) {
  var notes = _thinkingNotesForGroup(group);
  return notes.length ? String(notes[0].id || '') : '';
}


function thinkingReceiveScratchpadList(msg) {
  _thinkingEnsureState();
  var group = String((msg && msg.group) || _thinkingGroup() || '');
  var incoming = Array.isArray(msg && msg.notes) ? msg.notes : [];
  for (var id in state.thinking.scratchpad_notes) {
    var existing = state.thinking.scratchpad_notes[id];
    if (existing && _thinkingItemGroup(existing) === group) delete state.thinking.scratchpad_notes[id];
  }
  incoming.forEach(function(note) {
    if (note && note.id) state.thinking.scratchpad_notes[note.id] = Object.assign({}, note);
  });
  if (_thinkingScratchLoadingGroup === group) _thinkingScratchLoadingGroup = null;
  _thinkingScratchLoadedGroup = group;
  if (_thinkingScratchSelectedId && _thinkingScratchSelectedId !== THINKING_NEW_NOTE_ID) {
    var selected = state.thinking.scratchpad_notes[_thinkingScratchSelectedId];
    if (!selected || !_thinkingIsActive(selected) || _thinkingItemGroup(selected) !== group) {
      _thinkingScratchSelectedId = _thinkingFirstActiveNoteId(group);
    }
  }
  if (_thinkingPanelVisible()) renderThinkingPanel();
}

function thinkingReceiveScratchpadMutation(msg) {
  var note = msg && msg.note ? msg.note : null;
  if (!note || !note.id) return;
  _thinkingEnsureState();
  state.thinking.scratchpad_notes[note.id] = Object.assign({}, state.thinking.scratchpad_notes[note.id] || {}, note);
  _thinkingSaving = false;
  _thinkingLastError = '';
  if (msg.type === 'scratchpad_note_created') {
    delete _thinkingScratchDraftsById[THINKING_NEW_NOTE_ID];
    _thinkingScratchSelectedId = note.id;
  } else if (!_thinkingIsActive(note) && _thinkingScratchSelectedId === note.id) {
    delete _thinkingScratchDraftsById[note.id];
    _thinkingScratchSelectedId = _thinkingFirstActiveNoteId(_thinkingGroup());
  } else if (_thinkingScratchSelectedId === note.id) {
    delete _thinkingScratchDraftsById[note.id];
  }
  thinkingEnsureScratchpadLoaded({ force: true });
  if (_thinkingPanelVisible()) renderThinkingPanel();
}

function thinkingReceiveScratchpadDelta(note) {
  if (!note || !note.id) return;
  _thinkingEnsureState();
  state.thinking.scratchpad_notes[note.id] = Object.assign({}, state.thinking.scratchpad_notes[note.id] || {}, note);
  if (!_thinkingIsActive(note) && _thinkingScratchSelectedId === note.id) {
    _thinkingScratchSelectedId = _thinkingFirstActiveNoteId(_thinkingGroup());
  }
}
















function thinkingHandleError(msg) {
  if (!_thinkingPanelVisible()) return false;
  var age = _thinkingNowMs() - Number(_thinkingLastCommandAt || 0);
  var likelyThinking = age >= 0 && age < 15000;
  var text = String((msg && msg.message) || 'Thinking command failed.');
  if (!likelyThinking && !/Scratchpad|scratchpad|thinking/i.test(text)) return false;
  _thinkingSaving = false;
  _thinkingScratchLoadingGroup = null;
  _thinkingLastError = text;
  if (typeof renderThinkingPanel === 'function') renderThinkingPanel();
  return true;
}

function _thinkingEl(id) {
  return (typeof document !== 'undefined' && document && document.getElementById)
    ? document.getElementById(id)
    : null;
}

function _thinkingReadValue(id) {
  var el = _thinkingEl(id);
  return el && 'value' in el ? String(el.value || '') : '';
}

function _thinkingSetText(id, value) {
  var el = _thinkingEl(id);
  if (el) el.textContent = String(value || '');
}

function _thinkingCaptureScratchDraft() {
  if (!_thinkingScratchSelectedId) return;
  var titleEl = _thinkingEl('thinking-scratch-title');
  var bodyEl = _thinkingEl('thinking-scratch-body');
  if (!titleEl && !bodyEl) return;
  _thinkingScratchDraftsById[_thinkingScratchSelectedId] = {
    title: titleEl && 'value' in titleEl ? String(titleEl.value || '') : '',
    body: bodyEl && 'value' in bodyEl ? String(bodyEl.value || '') : '',
  };
}




function _thinkingCaptureDrafts() {
  _thinkingCaptureScratchDraft();
  _ideaBriefCaptureDraft();
}

function thinkingSetTab(tab) {
  tab = String(tab || 'scratchpad');
  if (THINKING_TABS.indexOf(tab) < 0) tab = 'scratchpad';
  _thinkingCaptureDrafts();
  _thinkingActiveTab = tab;
  if (tab === 'idea-briefs') {
    ideaBriefEnsureLoaded();
    thinkingEnsureScratchpadLoaded();
  } else thinkingEnsureScratchpadLoaded();
  renderThinkingPanel();
}
function thinkingScratchNew() {
  _thinkingCaptureDrafts();
  _thinkingScratchSelectedId = THINKING_NEW_NOTE_ID;
  if (!_thinkingScratchDraftsById[THINKING_NEW_NOTE_ID]) {
    _thinkingScratchDraftsById[THINKING_NEW_NOTE_ID] = { title: '', body: '' };
  }
  renderThinkingPanel();
}

function thinkingScratchSelect(noteId) {
  _thinkingCaptureDrafts();
  _thinkingScratchSelectedId = String(noteId || '');
  renderThinkingPanel();
}

function thinkingScratchChanged() {
  _thinkingCaptureScratchDraft();
  _thinkingSetText('thinking-scratch-draft-status', 'Unsaved rough note edits');
}

function _thinkingScratchDraft(noteId) {
  var draft = _thinkingScratchDraftsById[noteId];
  if (draft) return Object.assign({}, draft);
  var note = state && state.thinking && state.thinking.scratchpad_notes
    ? state.thinking.scratchpad_notes[noteId]
    : null;
  return {
    title: note ? String(note.title || '') : '',
    body: note ? String(note.body || '') : '',
  };
}

function _thinkingScratchIsDirty(noteId, draft) {
  if (noteId === THINKING_NEW_NOTE_ID) return !!(draft.title || draft.body);
  var note = state && state.thinking && state.thinking.scratchpad_notes
    ? state.thinking.scratchpad_notes[noteId]
    : null;
  if (!note) return false;
  return String(note.title || '') !== String(draft.title || '')
    || String(note.body || '') !== String(draft.body || '');
}

function thinkingScratchSave() {
  _thinkingCaptureScratchDraft();
  var noteId = String(_thinkingScratchSelectedId || '');
  if (!noteId) return;
  var draft = _thinkingScratchDraft(noteId);
  var title = String(draft.title || '').trim() || 'Untitled scratchpad note';
  if (noteId === THINKING_NEW_NOTE_ID) {
    _thinkingSend({
      cmd: 'scratchpad_note_create',
      group: _thinkingGroup(),
      title: title,
      body: draft.body || '',
      context: { source: 'thinking_panel', rough: true, handoff_ready: false },
      links: [],
    });
  } else {
    _thinkingSend({
      cmd: 'scratchpad_note_update',
      group: _thinkingGroup(),
      note_id: noteId,
      title: title,
      body: draft.body || '',
    });
  }
  renderThinkingPanel();
}

function _thinkingConfirm(message, opts) {
  if (typeof showConfirm === 'function') return showConfirm(message, opts || {});
  return Promise.resolve(true);
}

function thinkingScratchArchive(noteId) {
  noteId = String(noteId || _thinkingScratchSelectedId || '');
  if (!noteId || noteId === THINKING_NEW_NOTE_ID) return;
  _thinkingConfirm('Archive this scratchpad note? It will leave the default Thinking list.', {
    label: 'Archive', variant: 'btn-secondary', title: 'Archive scratchpad note'
  }).then(function(ok) {
    if (!ok) return;
    _thinkingSend({ cmd: 'scratchpad_note_archive', group: _thinkingGroup(), note_id: noteId });
    renderThinkingPanel();
  });
}

function thinkingScratchDelete(noteId) {
  noteId = String(noteId || _thinkingScratchSelectedId || '');
  if (!noteId || noteId === THINKING_NEW_NOTE_ID) return;
  _thinkingConfirm('Delete this scratchpad note? This is a soft delete but it will be hidden by default.', {
    label: 'Delete', variant: 'btn-danger', title: 'Delete scratchpad note'
  }).then(function(ok) {
    if (!ok) return;
    _thinkingSend({ cmd: 'scratchpad_note_delete', group: _thinkingGroup(), note_id: noteId });
    renderThinkingPanel();
  });
}





































function _renderThinkingTabs(noteTotal, briefTotal) {
  var active = String(_thinkingActiveTab || 'scratchpad');
  var html = '<div class="thinking-tabs ui-tablist" role="tablist" aria-label="Thinking surfaces" onkeydown="uiTablistKeydown(event)">';
  html += '<button type="button" role="tab" class="ui-tab ui-tab--contained thinking-tab' + (active === 'scratchpad' ? ' active' : '') + '" aria-selected="' + (active === 'scratchpad' ? 'true' : 'false') + '" tabindex="' + (active === 'scratchpad' ? '0' : '-1') + '" onclick="thinkingSetTab(\'scratchpad\')">Scratchpad <span class="thinking-tab-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count">' + _thinkingEsc(noteTotal) + '</span></button>';
  html += '<button type="button" role="tab" class="ui-tab ui-tab--contained thinking-tab' + (active === 'idea-briefs' ? ' active' : '') + '" aria-selected="' + (active === 'idea-briefs' ? 'true' : 'false') + '" tabindex="' + (active === 'idea-briefs' ? '0' : '-1') + '" onclick="thinkingSetTab(\'idea-briefs\')">Idea Briefs <span class="thinking-tab-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count">' + _thinkingEsc(briefTotal || 0) + '</span></button>';
  html += '</div>';
  return html;
}

function _renderThinkingListEmpty(kind) {
  return '<div class="thinking-empty ui-state ui-state--empty ui-state--compact">No active rough notes yet. Create a scratchpad note for loose thinking.</div>';
}

function _renderScratchpadList(group) {
  var notes = _thinkingNotesForGroup(group);
  if (!_thinkingScratchSelectedId && notes.length) _thinkingScratchSelectedId = String(notes[0].id || '');
  if (_thinkingScratchSelectedId && _thinkingScratchSelectedId !== THINKING_NEW_NOTE_ID) {
    var selected = state.thinking.scratchpad_notes[_thinkingScratchSelectedId];
    if (!selected || !_thinkingIsActive(selected) || _thinkingItemGroup(selected) !== group) {
      _thinkingScratchSelectedId = notes.length ? String(notes[0].id || '') : '';
    }
  }
  var html = '<aside class="thinking-list-pane thinking-scratch-list-pane">';
  html += '<div class="thinking-list-toolbar ui-toolbar ui-toolbar--bordered"><div><strong>Scratchpad</strong><span>Rough, group-scoped notes</span></div><button type="button" class="btn-primary" onclick="thinkingScratchNew()">New note</button></div>';
  if (_thinkingScratchLoadingGroup === group && _thinkingScratchLoadedGroup !== group) {
    html += '<div class="thinking-loading ui-state ui-state--loading ui-state--compact" role="status" aria-live="polite">Loading scratchpad notes…</div>';
  }
  html += '<div class="thinking-list" id="thinking-scratch-list">';
  if (_thinkingScratchSelectedId === THINKING_NEW_NOTE_ID) {
    html += '<button type="button" class="thinking-list-card selected draft" onclick="thinkingScratchSelect(\'' + _thinkingJs(THINKING_NEW_NOTE_ID) + '\')"><span class="thinking-list-title">New scratchpad note</span><span class="thinking-list-meta">Draft · not saved yet</span></button>';
  }
  if (!notes.length) html += _renderThinkingListEmpty('note');
  notes.forEach(function(note) {
    var noteId = String(note.id || '');
    var selected = _thinkingScratchSelectedId === noteId;
    html += '<button type="button" class="thinking-list-card' + (selected ? ' selected' : '') + '" onclick="thinkingScratchSelect(\'' + _thinkingJs(noteId) + '\')">';
    html += '<span class="thinking-list-title">' + _thinkingEsc(note.title || 'Untitled scratchpad note') + '</span>';
    var body = String(note.body || '').trim().split(/\n/)[0] || 'Rough note';
    html += '<span class="thinking-list-body">' + _thinkingEsc(body) + '</span>';
    html += '<span class="thinking-list-meta">' + _thinkingEsc(noteId + (_thinkingTimeLabel(note) ? ' · ' + _thinkingTimeLabel(note) : '')) + '</span>';
    html += '</button>';
  });
  html += '</div></aside>';
  return html;
}

function _renderScratchpadEditor() {
  var noteId = String(_thinkingScratchSelectedId || '');
  if (!noteId) {
    return '<section class="thinking-editor thinking-editor-empty"><div class="thinking-empty-detail ui-state ui-state--empty ui-state--fill">Select or create a scratchpad note. Notes stay informal and do not become tasks, decisions, journals, or Planning records.</div></section>';
  }
  var note = noteId === THINKING_NEW_NOTE_ID ? null : state.thinking.scratchpad_notes[noteId];
  if (noteId !== THINKING_NEW_NOTE_ID && (!note || !_thinkingIsActive(note))) {
    return '<section class="thinking-editor thinking-editor-empty"><div class="thinking-empty-detail ui-state ui-state--note ui-state--fill">This scratchpad note is no longer active.</div></section>';
  }
  var draft = _thinkingScratchDraft(noteId);
  var dirty = _thinkingScratchIsDirty(noteId, draft);
  var title = noteId === THINKING_NEW_NOTE_ID ? 'New scratchpad note' : (note.title || noteId);
  var status = noteId === THINKING_NEW_NOTE_ID ? 'Unsaved rough note' : (dirty ? 'Unsaved rough note edits' : 'Saved rough note');
  var html = '<section class="thinking-editor thinking-scratch-editor" id="thinking-scratch-editor">';
  html += '<div class="thinking-detail-head"><div><div class="thinking-kicker">Scratchpad note</div><h2>' + _thinkingEsc(title || 'Scratchpad note') + '</h2><div class="thinking-detail-id">' + _thinkingEsc(noteId === THINKING_NEW_NOTE_ID ? 'Draft' : noteId) + '</div></div>';
  if (noteId !== THINKING_NEW_NOTE_ID) html += '<button type="button" class="thinking-close" onclick="thinkingScratchSelect(\'\')" title="Close" aria-label="Close scratchpad details">×</button>';
  html += '</div>';
  html += '<div class="thinking-rough-banner"><strong>Rough note</strong><span>Useful scratch, not an execution artifact. Promote later by copying into the right tool when needed.</span></div>';
  html += '<div class="thinking-form">';
  html += '<label for="thinking-scratch-title">Title</label><input id="thinking-scratch-title" data-thinking-field="scratch-title:' + _thinkingAttr(noteId) + '" value="' + _thinkingAttr(draft.title || '') + '" oninput="thinkingScratchChanged()" autocomplete="off">';
  html += '<label for="thinking-scratch-body">Body</label><textarea id="thinking-scratch-body" data-thinking-field="scratch-body:' + _thinkingAttr(noteId) + '" rows="12" oninput="thinkingScratchChanged()" placeholder="Jot loose thinking, questions, snippets, or context…">' + _thinkingEsc(draft.body || '') + '</textarea>';
  html += '</div>';
  html += '<div class="thinking-detail-actions"><span class="thinking-save-status" id="thinking-scratch-draft-status">' + _thinkingEsc(status) + '</span>';
  if (noteId !== THINKING_NEW_NOTE_ID) {
    html += '<button type="button" class="btn-secondary" onclick="thinkingScratchArchive(\'' + _thinkingJs(noteId) + '\')">Archive</button>';
    html += '<button type="button" class="btn-secondary danger" onclick="thinkingScratchDelete(\'' + _thinkingJs(noteId) + '\')">Delete</button>';
  }
  html += '<button type="button" class="btn-primary" onclick="thinkingScratchSave()"' + (_thinkingSaving ? ' disabled' : '') + '>' + (_thinkingSaving ? 'Saving…' : (noteId === THINKING_NEW_NOTE_ID ? 'Create note' : 'Save note')) + '</button>';
  html += '</div></section>';
  return html;
}

function _renderThinkingScratchpad(group) {
  thinkingEnsureScratchpadLoaded();
  return '<div class="thinking-workspace thinking-scratch-workspace" id="thinking-workspace">'
    + _renderScratchpadList(group)
    + _renderScratchpadEditor()
    + '</div>';
}









function _ideaBriefActorPayload() {
  return { actor_kind: 'user', actor_id: '' };
}

function _ideaBriefSend(payload) {
  var data = Object.assign({}, payload || {}, _ideaBriefActorPayload());
  _ideaBriefSaving = true;
  _ideaBriefLastError = '';
  _ideaBriefLastCommandAt = _thinkingNowMs();
  if (typeof send === 'function') send(data);
}

function _ideaBriefItemGroup(item) {
  return String((item && (item.group || item.group_name)) || '');
}

function _ideaBriefStatus(item) {
  var status = String((item && item.status) || 'draft').toLowerCase();
  return IDEA_BRIEF_STATUS_ORDER.indexOf(status) >= 0 ? status : 'draft';
}

function _ideaBriefIsArchived(item) {
  return !!item && (_ideaBriefStatus(item) === 'archived'
    || !!String(item.archived_at || '').trim()
    || item.archived === true);
}

function _ideaBriefIsVisible(item, includeArchived) {
  if (!item || !item.id) return false;
  if (_ideaBriefIsArchived(item) && !includeArchived) return false;
  return true;
}

function _ideaBriefStatusLabel(status) {
  status = String(status || 'draft').toLowerCase();
  if (status === 'proposed') return 'Proposed for review';
  if (status === 'parked') return 'Parked';
  if (status === 'archived') return 'Archived';
  return 'Draft';
}

function _ideaBriefTimestamp(item) {
  var value = String((item && (item.updated_at || item.created_at)) || '');
  var parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function _ideaBriefsForGroup(group, includeArchived) {
  _ideaBriefEnsureState();
  var items = [];
  var g = String(group || '');
  var briefs = state.idea_briefs || {};
  for (var id in briefs) {
    var item = briefs[id];
    if (!item || !item.id) continue;
    if (g && _ideaBriefItemGroup(item) !== g) continue;
    if (!_ideaBriefIsVisible(item, !!includeArchived)) continue;
    items.push(item);
  }
  items.sort(function(a, b) {
    var as = IDEA_BRIEF_STATUS_ORDER.indexOf(_ideaBriefStatus(a));
    var bs = IDEA_BRIEF_STATUS_ORDER.indexOf(_ideaBriefStatus(b));
    if (as !== bs) return as - bs;
    var at = _ideaBriefTimestamp(a);
    var bt = _ideaBriefTimestamp(b);
    if (at !== bt) return bt - at;
    return String(a.title || a.id || '').localeCompare(String(b.title || b.id || ''));
  });
  return items;
}

function _ideaBriefFirstVisibleId(group) {
  var items = _ideaBriefsForGroup(group, _ideaBriefIncludeArchived);
  return items.length ? String(items[0].id || '') : '';
}

function _ideaBriefNeedsDetail(briefId) {
  return !!_ideaBriefSummaryIdsById[String(briefId || '')];
}

function _ideaBriefSurfaceStateOptions() {
  return {
    scrollSelectors: [
      ':root',
      '#thinking-workspace',
      '#thinking-scratch-list',
      '#thinking-scratch-editor',
      '#thinking-idea-list',
      '#thinking-idea-detail',
    ],
    captureFocusKey: function(active) {
      if (active && active.dataset && active.dataset.thinkingField) {
        return '[data-thinking-field="' + active.dataset.thinkingField + '"]';
      }
      return '';
    },
  };
}

function _ideaBriefCapturePendingDetailSurfaceState(briefId) {
  if (!_thinkingPanelVisible() || !briefId || typeof _captureSurfaceState !== 'function') return null;
  var panel = document.getElementById('panel-thinking');
  if (!panel) return null;
  return {
    briefId: String(briefId),
    snapshot: _captureSurfaceState(panel, _ideaBriefSurfaceStateOptions()),
  };
}

function ideaBriefEnsureLoaded(opts) {
  opts = opts || {};
  var group = _thinkingGroup();
  if (!group && !opts.force) return false;
  var loadKey = group + '|' + (_ideaBriefIncludeArchived ? '1' : '0');
  if (!opts.force && (_ideaBriefLoadedGroup === loadKey || _ideaBriefLoadingGroup === loadKey)) return false;
  _ideaBriefLoadingGroup = loadKey;
  _ideaBriefLastError = '';
  if (typeof send === 'function') {
    send({
      cmd: 'idea_brief_list',
      group: group,
      include_archived: !!_ideaBriefIncludeArchived,
    });
  }
  return true;
}

function ideaBriefReceiveList(msg) {
  _ideaBriefEnsureState();
  var group = String((msg && msg.group) || _thinkingGroup() || '');
  var includeArchived = !!_ideaBriefIncludeArchived;
  var loadKey = group + '|' + (includeArchived ? '1' : '0');
  var incoming = Array.isArray(msg && msg.idea_briefs) ? msg.idea_briefs : [];
  var selectedBeforeList = String(_ideaBriefSelectedId || '');
  var pendingSurfaceState = _ideaBriefCapturePendingDetailSurfaceState(selectedBeforeList);
  _ideaBriefCaptureDraft();
  for (var id in state.idea_briefs) {
    var existing = state.idea_briefs[id];
    if (existing && _ideaBriefItemGroup(existing) === group) {
      delete state.idea_briefs[id];
      delete _ideaBriefSummaryIdsById[id];
    }
  }
  incoming.forEach(function(brief) {
    if (brief && brief.id) {
      state.idea_briefs[brief.id] = Object.assign({}, brief);
      _ideaBriefSummaryIdsById[brief.id] = true;
    }
  });
  if (_ideaBriefLoadingGroup === loadKey) _ideaBriefLoadingGroup = null;
  _ideaBriefLoadedGroup = loadKey;
  if (!_ideaBriefSelectedId) {
    _ideaBriefSelectedId = _ideaBriefFirstVisibleId(group);
  } else if (_ideaBriefSelectedId !== THINKING_NEW_IDEA_BRIEF_ID) {
    var selected = state.idea_briefs[_ideaBriefSelectedId];
    if (!selected || _ideaBriefItemGroup(selected) !== group || !_ideaBriefIsVisible(selected, includeArchived)) {
      _ideaBriefSelectedId = _ideaBriefFirstVisibleId(group);
      _ideaBriefSelectedLinkKey = '';
    }
  }
  if (selectedBeforeList !== _ideaBriefSelectedId) _ideaBriefPendingDetailSurfaceState = null;
  else if (_ideaBriefSelectedId && _ideaBriefSelectedId !== THINKING_NEW_IDEA_BRIEF_ID) {
    // A clean server value should be replaced by the named detail response.
    // Only carry a snapshot through the loading shell when it protects an
    // actual local draft (including its focus, caret, and scroll position).
    _ideaBriefPendingDetailSurfaceState = _ideaBriefDirtyDraftsById[_ideaBriefSelectedId]
      ? pendingSurfaceState
      : null;
  }
  if (_ideaBriefSelectedId && _ideaBriefSelectedId !== THINKING_NEW_IDEA_BRIEF_ID) {
    ideaBriefLoad(_ideaBriefSelectedId);
  }
  if (_thinkingPanelVisible()) renderThinkingPanel();
}

function _ideaBriefFromMessage(msg) {
  if (!msg) return null;
  if (msg.idea_brief) return msg.idea_brief;
  if (msg.type === 'idea_brief' && msg.id) {
    var payload = Object.assign({}, msg);
    delete payload.type;
    return payload;
  }
  return null;
}

function ideaBriefReceiveMutation(msg) {
  var brief = _ideaBriefFromMessage(msg);
  if (!brief || !brief.id) return;
  _ideaBriefEnsureState();
  state.idea_briefs[brief.id] = Object.assign({}, state.idea_briefs[brief.id] || {}, brief);
  delete _ideaBriefSummaryIdsById[brief.id];
  if (_ideaBriefShowLoadingId === brief.id) _ideaBriefShowLoadingId = '';
  _ideaBriefSaving = false;
  _ideaBriefLastError = '';
  var verb = 'updated';
  if (msg.type === 'idea_brief_created') {
    verb = 'created';
    delete _ideaBriefDraftsById[THINKING_NEW_IDEA_BRIEF_ID];
    delete _ideaBriefDirtyDraftsById[THINKING_NEW_IDEA_BRIEF_ID];
    delete _ideaBriefRenderedDraftsById[THINKING_NEW_IDEA_BRIEF_ID];
    _ideaBriefSelectedId = brief.id;
  } else if (msg.type === 'idea_brief_refined') {
    verb = 'refined';
  } else if (msg.type === 'idea_brief_parked') {
    verb = 'parked';
  } else if (msg.type === 'idea_brief_archived') {
    verb = 'archived';
  } else if (msg.type === 'idea_brief_proposed') {
    verb = 'proposed for product-safe review';
    _ideaBriefProposalResultById[brief.id] = {
      caveat: String(msg.caveat || ''),
      review_scope: String(msg.review_scope || ''),
      proposal: msg.proposal || brief.proposal || {},
    };
  }
  if (msg.type !== 'idea_brief') {
    _ideaBriefLastStatus = 'Idea Brief ' + verb + '.';
    delete _ideaBriefDraftsById[brief.id];
    delete _ideaBriefDirtyDraftsById[brief.id];
  }
  if (!_ideaBriefSelectedId || _ideaBriefSelectedId === THINKING_NEW_IDEA_BRIEF_ID) {
    _ideaBriefSelectedId = brief.id;
  }
  if (msg.type === 'idea_brief_archived' && !_ideaBriefIncludeArchived) {
    _ideaBriefSelectedId = _ideaBriefFirstVisibleId(_thinkingGroup());
  }
  // A named show response is already the complete selected detail. Refreshing
  // its list here would replace it with another summary row and trigger an
  // avoidable list/show loop.
  if (msg.type !== 'idea_brief') ideaBriefEnsureLoaded({ force: true });
  if (_thinkingPanelVisible()) renderThinkingPanel();
}

function ideaBriefReceiveDelta(brief) {
  if (!brief || !brief.id) return;
  _ideaBriefEnsureState();
  state.idea_briefs[brief.id] = Object.assign({}, state.idea_briefs[brief.id] || {}, brief);
  if (_ideaBriefSelectedId === brief.id && !_ideaBriefIsVisible(state.idea_briefs[brief.id], _ideaBriefIncludeArchived)) {
    _ideaBriefSelectedId = _ideaBriefFirstVisibleId(_thinkingGroup());
    _ideaBriefSelectedLinkKey = '';
  }
}

function ideaBriefHandleError(msg) {
  if (!msg || msg.contract !== 'torque.idea_brief.v1') return false;
  var age = _thinkingNowMs() - Number(_ideaBriefLastCommandAt || 0);
  var likelyIdeaBrief = age >= 0 && age < 15000;
  if (!_thinkingPanelVisible() && !likelyIdeaBrief) return false;
  _ideaBriefSaving = false;
  _ideaBriefLoadingGroup = null;
  _ideaBriefShowLoadingId = '';
  var code = String(msg.code || 'idea_brief_error');
  var text = String(msg.message || 'Idea Brief command failed.');
  _ideaBriefLastError = code ? (code + ': ' + text) : text;
  if (_thinkingPanelVisible() && typeof renderThinkingPanel === 'function') renderThinkingPanel();
  return true;
}

function ideaBriefRefresh() {
  _thinkingCaptureDrafts();
  ideaBriefEnsureLoaded({ force: true });
  thinkingEnsureScratchpadLoaded({ force: true });
  if (_ideaBriefSelectedId && _ideaBriefSelectedId !== THINKING_NEW_IDEA_BRIEF_ID) {
    ideaBriefLoad(_ideaBriefSelectedId, { force: true });
  }
  renderThinkingPanel();
}

function ideaBriefToggleArchived() {
  _thinkingCaptureDrafts();
  _ideaBriefIncludeArchived = !_ideaBriefIncludeArchived;
  _ideaBriefSelectedId = '';
  _ideaBriefSelectedLinkKey = '';
  ideaBriefEnsureLoaded({ force: true });
  renderThinkingPanel();
}

function ideaBriefNew() {
  _thinkingCaptureDrafts();
  _ideaBriefLastStatus = '';
  _ideaBriefPendingDetailSurfaceState = null;
  _ideaBriefSelectedId = THINKING_NEW_IDEA_BRIEF_ID;
  _ideaBriefSelectedLinkKey = '';
  if (!_ideaBriefDraftsById[THINKING_NEW_IDEA_BRIEF_ID]) {
    _ideaBriefDraftsById[THINKING_NEW_IDEA_BRIEF_ID] = _ideaBriefBlankDraft();
  }
  renderThinkingPanel();
}

function ideaBriefSelect(briefId) {
  _thinkingCaptureDrafts();
  _ideaBriefLastStatus = '';
  _ideaBriefSelectedId = String(briefId || '');
  _ideaBriefPendingDetailSurfaceState = null;
  _ideaBriefSelectedLinkKey = '';
  if (_ideaBriefSelectedId && _ideaBriefSelectedId !== THINKING_NEW_IDEA_BRIEF_ID) {
    ideaBriefLoad(_ideaBriefSelectedId);
  }
  renderThinkingPanel();
}

function ideaBriefLoad(briefId, opts) {
  opts = opts || {};
  briefId = String(briefId || _ideaBriefSelectedId || '').trim();
  if (!briefId || briefId === THINKING_NEW_IDEA_BRIEF_ID) return false;
  if (!opts.force && _ideaBriefShowLoadingId === briefId) return false;
  _ideaBriefShowLoadingId = briefId;
  if (typeof send === 'function') {
    send({
      cmd: 'idea_brief_show',
      group: _thinkingGroup(),
      idea_brief: briefId,
      include_archived: !!_ideaBriefIncludeArchived,
    });
  }
  return true;
}

function ideaBriefChanged() {
  _ideaBriefCaptureDraft();
  _thinkingSetText('idea-brief-draft-status', 'Unsaved Idea Brief edits');
}

function ideaBriefSelectLink(key) {
  _ideaBriefSelectedLinkKey = String(key || '');
  renderThinkingPanel();
}

function _ideaBriefCopyLinks(value) {
  if (!Array.isArray(value)) return [];
  return value.map(function(link) { return Object.assign({}, link || {}); });
}

function _ideaBriefBlankDraft() {
  return {
    title: '',
    problem_opportunity: '',
    why_it_matters: '',
    proposed_shape: '',
    smallest_useful_version: '',
    risks_tradeoffs: '',
    open_questions: '',
    thinking_links: [],
    refinement_note: '',
    lifecycle_reason: '',
    proposal_note: '',
    link_source_key: '',
    link_context: '',
  };
}

function _ideaBriefDraftFromRow(row) {
  var draft = _ideaBriefBlankDraft();
  IDEA_BRIEF_FIELDS.forEach(function(field) {
    draft[field] = String((row && row[field]) || '');
  });
  draft.thinking_links = _ideaBriefCopyLinks(row && row.thinking_links);
  return draft;
}

function _ideaBriefDraftSnapshot(draft) {
  var snapshot = _ideaBriefBlankDraft();
  IDEA_BRIEF_FIELDS.forEach(function(field) {
    snapshot[field] = String((draft && draft[field]) || '');
  });
  snapshot.thinking_links = _ideaBriefCopyLinks(draft && draft.thinking_links);
  snapshot.refinement_note = String((draft && draft.refinement_note) || '');
  snapshot.lifecycle_reason = String((draft && draft.lifecycle_reason) || '');
  snapshot.proposal_note = String((draft && draft.proposal_note) || '');
  snapshot.link_source_key = String((draft && draft.link_source_key) || '');
  snapshot.link_context = String((draft && draft.link_context) || '');
  return snapshot;
}

function _ideaBriefDraftSnapshotsEqual(a, b) {
  var left = _ideaBriefDraftSnapshot(a);
  var right = _ideaBriefDraftSnapshot(b);
  var fields = IDEA_BRIEF_FIELDS.concat([
    'refinement_note',
    'lifecycle_reason',
    'proposal_note',
    'link_source_key',
    'link_context',
  ]);
  for (var i = 0; i < fields.length; i++) {
    var field = fields[i];
    if (String(left[field] || '') !== String(right[field] || '')) return false;
  }
  return JSON.stringify(_ideaBriefCopyLinks(left.thinking_links))
    === JSON.stringify(_ideaBriefCopyLinks(right.thinking_links));
}

function _ideaBriefDefaultLinkSourceKey(draft) {
  var links = _ideaBriefCopyLinks(draft && draft.thinking_links);
  var sources = _ideaBriefLinkSources(_thinkingGroup(), links);
  return String((sources[0] && sources[0].key) || '');
}

function _ideaBriefDraftHasNonDefaultLinkSource(draft) {
  var selected = String((draft && draft.link_source_key) || '');
  if (!selected) return false;
  return selected !== _ideaBriefDefaultLinkSourceKey(draft);
}

function _ideaBriefDraftHasTransientState(draft) {
  return !!(
    String((draft && draft.refinement_note) || '').trim()
    || String((draft && draft.lifecycle_reason) || '').trim()
    || String((draft && draft.proposal_note) || '').trim()
    || _ideaBriefDraftHasNonDefaultLinkSource(draft)
    || String((draft && draft.link_context) || '').trim()
  );
}

function _ideaBriefDraftHasLocalContent(briefId, draft) {
  if (briefId === THINKING_NEW_IDEA_BRIEF_ID) {
    return IDEA_BRIEF_FIELDS.some(function(field) { return !!String((draft && draft[field]) || '').trim(); })
      || _ideaBriefCopyLinks(draft && draft.thinking_links).length > 0
      || _ideaBriefDraftHasTransientState(draft);
  }
  return _ideaBriefDraftIsDirty(briefId, draft) || _ideaBriefDraftHasTransientState(draft);
}

function _ideaBriefDraft(briefId) {
  briefId = String(briefId || _ideaBriefSelectedId || '');
  if (_ideaBriefDraftsById[briefId]) return Object.assign({}, _ideaBriefDraftsById[briefId], {
    thinking_links: _ideaBriefCopyLinks(_ideaBriefDraftsById[briefId].thinking_links),
  });
  var row = briefId === THINKING_NEW_IDEA_BRIEF_ID ? null : (state && state.idea_briefs ? state.idea_briefs[briefId] : null);
  return _ideaBriefDraftFromRow(row);
}

function _ideaBriefRenderedDraftIdFromDom() {
  var ids = IDEA_BRIEF_FIELDS.map(function(field) {
    return 'idea-brief-' + field.replace(/_/g, '-');
  }).concat([
    'idea-brief-refinement-note',
    'idea-brief-lifecycle-reason',
    'idea-brief-proposal-note',
    'idea-brief-link-source',
    'idea-brief-link-context',
  ]);
  for (var i = 0; i < ids.length; i++) {
    var el = _thinkingEl(ids[i]);
    var key = el && el.dataset ? String(el.dataset.thinkingField || '') : '';
    var match = key.match(/^idea-brief:(.*):[^:]+$/);
    if (match) return match[1];
  }
  return '';
}

function _ideaBriefCaptureDraft() {
  var briefId = String(_ideaBriefRenderedDraftIdFromDom() || _ideaBriefSelectedId || '');
  if (!briefId) return;
  var hasField = false;
  var renderedDraft = _ideaBriefRenderedDraftsById[briefId] || null;
  var draft = _ideaBriefDraftsById[briefId]
    ? _ideaBriefDraft(briefId)
    : (renderedDraft ? _ideaBriefDraftSnapshot(renderedDraft) : _ideaBriefDraft(briefId));
  IDEA_BRIEF_FIELDS.forEach(function(field) {
    var el = _thinkingEl('idea-brief-' + field.replace(/_/g, '-'));
    if (el && 'value' in el) {
      draft[field] = String(el.value || '');
      hasField = true;
    }
  });
  var refineEl = _thinkingEl('idea-brief-refinement-note');
  var reasonEl = _thinkingEl('idea-brief-lifecycle-reason');
  var proposalEl = _thinkingEl('idea-brief-proposal-note');
  var sourceEl = _thinkingEl('idea-brief-link-source');
  var contextEl = _thinkingEl('idea-brief-link-context');
  if (refineEl && 'value' in refineEl) { draft.refinement_note = String(refineEl.value || ''); hasField = true; }
  if (reasonEl && 'value' in reasonEl) { draft.lifecycle_reason = String(reasonEl.value || ''); hasField = true; }
  if (proposalEl && 'value' in proposalEl) { draft.proposal_note = String(proposalEl.value || ''); hasField = true; }
  if (sourceEl && 'value' in sourceEl) { draft.link_source_key = String(sourceEl.value || ''); hasField = true; }
  if (contextEl && 'value' in contextEl) { draft.link_context = String(contextEl.value || ''); hasField = true; }
  if (!hasField) return;
  draft = _ideaBriefDraftSnapshot(draft);
  var changedSinceRender = renderedDraft
    ? !_ideaBriefDraftSnapshotsEqual(draft, renderedDraft)
    : _ideaBriefDraftHasLocalContent(briefId, draft);
  if (changedSinceRender) {
    if (_ideaBriefDraftHasLocalContent(briefId, draft)) _ideaBriefDirtyDraftsById[briefId] = true;
    else delete _ideaBriefDirtyDraftsById[briefId];
  }
  if (_ideaBriefDirtyDraftsById[briefId] && _ideaBriefDraftHasLocalContent(briefId, draft)) {
    _ideaBriefDraftsById[briefId] = draft;
  } else {
    delete _ideaBriefDraftsById[briefId];
    delete _ideaBriefDirtyDraftsById[briefId];
  }
}

function _ideaBriefDraftIsDirty(briefId, draft) {
  if (briefId === THINKING_NEW_IDEA_BRIEF_ID) {
    return IDEA_BRIEF_FIELDS.some(function(field) { return !!String(draft[field] || '').trim(); })
      || (draft.thinking_links || []).length > 0;
  }
  var row = state && state.idea_briefs ? state.idea_briefs[briefId] : null;
  if (!row) return false;
  for (var i = 0; i < IDEA_BRIEF_FIELDS.length; i++) {
    var field = IDEA_BRIEF_FIELDS[i];
    if (String(row[field] || '') !== String(draft[field] || '')) return true;
  }
  return JSON.stringify(_ideaBriefCopyLinks(row.thinking_links)) !== JSON.stringify(_ideaBriefCopyLinks(draft.thinking_links));
}

function _ideaBriefPayloadFromDraft(draft) {
  var payload = {};
  IDEA_BRIEF_FIELDS.forEach(function(field) {
    payload[field] = String((draft && draft[field]) || '');
  });
  payload.title = String(payload.title || '').trim()
    || String(payload.problem_opportunity || '').trim().split(/\n/)[0].slice(0, 80)
    || 'Untitled Idea Brief';
  payload.thinking_links = _ideaBriefCopyLinks(draft && draft.thinking_links);
  return payload;
}

function ideaBriefSave() {
  _ideaBriefCaptureDraft();
  var briefId = String(_ideaBriefSelectedId || '');
  if (!briefId) return;
  if (briefId !== THINKING_NEW_IDEA_BRIEF_ID && _ideaBriefNeedsDetail(briefId)) {
    ideaBriefLoad(briefId);
    return;
  }
  var draft = _ideaBriefDraft(briefId);
  if (!String(draft.problem_opportunity || '').trim()) {
    _ideaBriefLastError = 'validation_error: Problem or opportunity is required.';
    renderThinkingPanel();
    return;
  }
  var payload = _ideaBriefPayloadFromDraft(draft);
  payload.group = _thinkingGroup();
  if (briefId === THINKING_NEW_IDEA_BRIEF_ID) {
    payload.cmd = 'idea_brief_create';
  } else {
    payload.cmd = 'idea_brief_update';
    payload.idea_brief = briefId;
  }
  _ideaBriefSend(payload);
  renderThinkingPanel();
}

function ideaBriefRefine() {
  _ideaBriefCaptureDraft();
  var briefId = String(_ideaBriefSelectedId || '');
  if (!briefId || briefId === THINKING_NEW_IDEA_BRIEF_ID) return;
  if (_ideaBriefNeedsDetail(briefId)) {
    ideaBriefLoad(briefId);
    return;
  }
  var draft = _ideaBriefDraft(briefId);
  var payload = _ideaBriefPayloadFromDraft(draft);
  payload.cmd = 'idea_brief_refine';
  payload.group = _thinkingGroup();
  payload.idea_brief = briefId;
  payload.refinement_note = String(draft.refinement_note || '');
  _ideaBriefSend(payload);
  renderThinkingPanel();
}

function _ideaBriefConfirm(message, opts) {
  if (typeof showConfirm === 'function') return showConfirm(message, opts || {});
  return Promise.resolve(true);
}

function ideaBriefPark() {
  _ideaBriefCaptureDraft();
  var briefId = String(_ideaBriefSelectedId || '');
  if (!briefId || briefId === THINKING_NEW_IDEA_BRIEF_ID) return;
  if (_ideaBriefNeedsDetail(briefId)) {
    ideaBriefLoad(briefId);
    return;
  }
  var draft = _ideaBriefDraft(briefId);
  _ideaBriefConfirm('Park this Idea Brief? It stays available for later review.', {
    label: 'Park', variant: 'btn-secondary', title: 'Park Idea Brief'
  }).then(function(ok) {
    if (!ok) return;
    _ideaBriefSend({ cmd: 'idea_brief_park', group: _thinkingGroup(), idea_brief: briefId, reason: draft.lifecycle_reason || '' });
    renderThinkingPanel();
  });
}

function ideaBriefArchive() {
  _ideaBriefCaptureDraft();
  var briefId = String(_ideaBriefSelectedId || '');
  if (!briefId || briefId === THINKING_NEW_IDEA_BRIEF_ID) return;
  if (_ideaBriefNeedsDetail(briefId)) {
    ideaBriefLoad(briefId);
    return;
  }
  var draft = _ideaBriefDraft(briefId);
  _ideaBriefConfirm('Archive this Idea Brief? Archived briefs are hidden by default.', {
    label: 'Archive', variant: 'btn-danger', title: 'Archive Idea Brief'
  }).then(function(ok) {
    if (!ok) return;
    _ideaBriefSend({ cmd: 'idea_brief_archive', group: _thinkingGroup(), idea_brief: briefId, reason: draft.lifecycle_reason || '' });
    renderThinkingPanel();
  });
}

function ideaBriefPropose() {
  _ideaBriefCaptureDraft();
  var briefId = String(_ideaBriefSelectedId || '');
  if (!briefId || briefId === THINKING_NEW_IDEA_BRIEF_ID) return;
  if (_ideaBriefNeedsDetail(briefId)) {
    ideaBriefLoad(briefId);
    return;
  }
  var draft = _ideaBriefDraft(briefId);
  _ideaBriefConfirm('Propose this Idea Brief for product-safe review? This will not create tasks, assign work, dispatch agents, or accept a decision.', {
    label: 'Propose for review', variant: 'btn-primary', title: 'Propose Idea Brief'
  }).then(function(ok) {
    if (!ok) return;
    _ideaBriefSend({ cmd: 'idea_brief_propose', group: _thinkingGroup(), idea_brief: briefId, note: draft.proposal_note || '' });
    renderThinkingPanel();
  });
}

function _ideaBriefLinkKey(link, index) {
  return [
    String((link && link.type) || ''),
    String((link && link.id) || ''),
    String((link && link.map_id) || ''),
    String(index || 0),
  ].join('|');
}

function _ideaBriefLinkTypeLabel(type) {
  return String(type || '') === 'scratchpad_note' ? 'Scratchpad note' : 'Thinking link';
}
function _ideaBriefLinkTitle(link) {
  if (!link) return 'Linked Thinking';
  return String(link.title || link.map_title || link.label || link.summary || link.id || 'Linked Thinking');
}

function _ideaBriefLinkSubtitle(link) {
  return link ? _ideaBriefLinkTypeLabel(link.type) : '';
}
function _ideaBriefLinkMetadata(link) {
  var keys = ['context', 'source_context', 'summary', 'reason', 'quote', 'selection', 'note', 'confidence'];
  var out = [];
  keys.forEach(function(key) {
    var value = link && link[key];
    if (value == null || value === '') return;
    if (typeof value === 'object') {
      try { value = JSON.stringify(value); } catch (_err) { value = String(value); }
    }
    out.push({ key: key, value: String(value) });
  });
  return out;
}

function _ideaBriefKnownLinkIds(links) {
  var seen = {};
  (links || []).forEach(function(link) {
    var key = String((link && link.type) || '') + '|' + String((link && link.id) || '');
    seen[key] = true;
  });
  return seen;
}

function _ideaBriefLinkSources(group, existingLinks) {
  _thinkingEnsureState();
  var seen = _ideaBriefKnownLinkIds(existingLinks || []);
  var sources = [];
  _thinkingNotesForGroup(group).forEach(function(note) {
    var key = 'scratchpad_note|' + String(note.id || '');
    if (seen[key]) return;
    sources.push({ key: key, label: 'Scratchpad · ' + (note.title || note.id || 'Untitled note'), payload: {
      type: 'scratchpad_note', id: note.id, title: note.title || '', slug: note.slug || '',
      group: _thinkingItemGroup(note) || group, archived: !!note.archived,
      summary: String(note.body || '').trim().split(/\n/)[0].slice(0, 140),
    }});
  });
  return sources;
}
function ideaBriefAddLink() {
  _ideaBriefCaptureDraft();
  var briefId = String(_ideaBriefSelectedId || '');
  if (!briefId) return;
  var draft = _ideaBriefDraft(briefId);
  var sources = _ideaBriefLinkSources(_thinkingGroup(), draft.thinking_links);
  var selectedKey = String(draft.link_source_key || (sources[0] && sources[0].key) || '');
  var source = null;
  for (var i = 0; i < sources.length; i++) {
    if (sources[i].key === selectedKey) {
      source = sources[i];
      break;
    }
  }
  if (!source) {
    _ideaBriefLastError = 'validation_error: Choose a Thinking artifact to link.';
    renderThinkingPanel();
    return;
  }
  var link = Object.assign({}, source.payload || {});
  var context = String(draft.link_context || '').trim();
  if (context) link.context = context;
  draft.thinking_links = _ideaBriefCopyLinks(draft.thinking_links);
  draft.thinking_links.push(link);
  draft.link_source_key = '';
  draft.link_context = '';
  _ideaBriefDraftsById[briefId] = draft;
  _ideaBriefDirtyDraftsById[briefId] = true;
  _ideaBriefSelectedLinkKey = _ideaBriefLinkKey(link, draft.thinking_links.length - 1);
  renderThinkingPanel();
}

function ideaBriefRemoveLink(index) {
  _ideaBriefCaptureDraft();
  var briefId = String(_ideaBriefSelectedId || '');
  if (!briefId) return;
  var draft = _ideaBriefDraft(briefId);
  var idx = Number(index);
  if (!Number.isFinite(idx) || idx < 0 || idx >= (draft.thinking_links || []).length) return;
  draft.thinking_links = _ideaBriefCopyLinks(draft.thinking_links);
  draft.thinking_links.splice(idx, 1);
  _ideaBriefDraftsById[briefId] = draft;
  _ideaBriefDirtyDraftsById[briefId] = true;
  _ideaBriefSelectedLinkKey = '';
  renderThinkingPanel();
}

function ideaBriefOpenThinkingLink(index) {
  _ideaBriefCaptureDraft();
  var briefId = String(_ideaBriefSelectedId || '');
  var draft = _ideaBriefDraft(briefId);
  var link = (draft.thinking_links || [])[Number(index)];
  if (!link || String(link.type || '') !== 'scratchpad_note') return;
  _thinkingActiveTab = 'scratchpad';
  _thinkingScratchSelectedId = String(link.id || '');
  thinkingEnsureScratchpadLoaded();
  renderThinkingPanel();
}
function _renderIdeaBriefList(group) {
  var briefs = _ideaBriefsForGroup(group, _ideaBriefIncludeArchived);
  if (!_ideaBriefSelectedId && briefs.length) _ideaBriefSelectedId = String(briefs[0].id || '');
  if (_ideaBriefSelectedId && _ideaBriefSelectedId !== THINKING_NEW_IDEA_BRIEF_ID) {
    var selected = state.idea_briefs[_ideaBriefSelectedId];
    if (!selected || _ideaBriefItemGroup(selected) !== group || !_ideaBriefIsVisible(selected, _ideaBriefIncludeArchived)) {
      _ideaBriefSelectedId = briefs.length ? String(briefs[0].id || '') : '';
      _ideaBriefSelectedLinkKey = '';
    }
  }
  var loadKey = group + '|' + (_ideaBriefIncludeArchived ? '1' : '0');
  var html = '<aside class="thinking-list-pane idea-brief-list-pane">';
  html += '<div class="thinking-list-toolbar idea-brief-list-toolbar ui-toolbar ui-toolbar--bordered"><div><strong>Idea Briefs</strong><span>Opportunity proposals linked to Thinking</span></div><button type="button" class="btn-primary" onclick="ideaBriefNew()">New brief</button></div>';
  html += '<div class="idea-brief-filter-row"><button type="button" class="filter-chip' + (_ideaBriefIncludeArchived ? ' active' : '') + '" aria-pressed="' + (_ideaBriefIncludeArchived ? 'true' : 'false') + '" onclick="ideaBriefToggleArchived()">' + (_ideaBriefIncludeArchived ? 'Hide archived' : 'Show archived') + '</button><button type="button" class="btn btn-secondary btn-sm" onclick="ideaBriefRefresh()">Refresh</button></div>';
  if (_ideaBriefLoadingGroup === loadKey && _ideaBriefLoadedGroup !== loadKey) {
    html += '<div class="thinking-loading ui-state ui-state--loading ui-state--compact" role="status" aria-live="polite">Loading Idea Briefs…</div>';
  }
  html += '<div class="thinking-list idea-brief-list" id="thinking-idea-list">';
  if (_ideaBriefSelectedId === THINKING_NEW_IDEA_BRIEF_ID) {
    html += '<button type="button" class="thinking-list-card idea-brief-card selected draft" onclick="ideaBriefSelect(\'' + _thinkingJs(THINKING_NEW_IDEA_BRIEF_ID) + '\')"><span class="thinking-list-title">New Idea Brief</span><span class="thinking-list-meta">Draft · not saved yet</span></button>';
  }
  if (!briefs.length) html += '<div class="thinking-empty ui-state ui-state--empty ui-state--compact">No Idea Briefs yet. Create one from a problem, opportunity, or linked Thinking artifact.</div>';
  briefs.forEach(function(brief) {
    var briefId = String(brief.id || '');
    var selected = _ideaBriefSelectedId === briefId;
    var status = _ideaBriefStatus(brief);
    html += '<button type="button" class="thinking-list-card idea-brief-card idea-brief-status-' + _thinkingAttr(status) + (selected ? ' selected' : '') + '" onclick="ideaBriefSelect(\'' + _thinkingJs(briefId) + '\')">';
    html += '<span class="thinking-list-title">' + _thinkingEsc(brief.title || 'Untitled Idea Brief') + '</span>';
    html += '<span class="thinking-list-body">' + _thinkingEsc(brief.problem_opportunity || brief.proposed_shape || 'Opportunity proposal') + '</span>';
    html += '<span class="thinking-list-meta"><span class="idea-brief-status-pill">' + _thinkingEsc(_ideaBriefStatusLabel(status)) + '</span> ' + _thinkingEsc(briefId + (_thinkingTimeLabel(brief) ? ' · ' + _thinkingTimeLabel(brief) : '')) + '</span>';
    html += '</button>';
  });
  html += '</div></aside>';
  return html;
}

function _renderIdeaBriefField(field, draft) {
  var labels = {
    title: 'Title',
    problem_opportunity: 'Problem or opportunity',
    why_it_matters: 'Why it matters',
    proposed_shape: 'Proposed shape',
    smallest_useful_version: 'Smallest useful version',
    risks_tradeoffs: 'Risks and tradeoffs',
    open_questions: 'Open questions',
  };
  var placeholders = {
    title: 'Short operator-facing name',
    problem_opportunity: 'What problem, opportunity, or user need is this brief about?',
    why_it_matters: 'Why should Blueprint/Torqly/user care now?',
    proposed_shape: 'What might the product or workflow look like?',
    smallest_useful_version: 'What is the smallest reviewable version?',
    risks_tradeoffs: 'Known risks, tradeoffs, and caveats',
    open_questions: 'Questions to answer before accepting scope',
  };
  var domId = 'idea-brief-' + field.replace(/_/g, '-');
  var rows = field === 'title' ? 0 : (field === 'problem_opportunity' ? 4 : 3);
  var html = '<label for="' + _thinkingAttr(domId) + '">' + _thinkingEsc(labels[field] || field) + '</label>';
  if (field === 'title') {
    html += '<input id="' + _thinkingAttr(domId) + '" data-thinking-field="idea-brief:' + _thinkingAttr(_ideaBriefSelectedId) + ':' + _thinkingAttr(field) + '" value="' + _thinkingAttr(draft[field] || '') + '" oninput="ideaBriefChanged()" autocomplete="off" placeholder="' + _thinkingAttr(placeholders[field] || '') + '">';
  } else {
    html += '<textarea id="' + _thinkingAttr(domId) + '" data-thinking-field="idea-brief:' + _thinkingAttr(_ideaBriefSelectedId) + ':' + _thinkingAttr(field) + '" rows="' + _thinkingAttr(rows) + '" oninput="ideaBriefChanged()" placeholder="' + _thinkingAttr(placeholders[field] || '') + '">' + _thinkingEsc(draft[field] || '') + '</textarea>';
  }
  return html;
}

function _renderIdeaBriefProposalBanner(brief, proposalResult) {
  var proposal = (proposalResult && proposalResult.proposal) || (brief && brief.proposal) || {};
  var status = _ideaBriefStatus(brief);
  if (status !== 'proposed' && !proposal.proposal_only && !proposalResult) return '';
  var note = String((proposalResult && proposalResult.caveat) || '');
  if (!note) note = 'Proposed for product-safe review only. No task, assignment, dispatch, accepted decision, merge, or deploy action was created.';
  var html = '<div class="idea-brief-proposal-banner"><strong>Proposal for review</strong><span>' + _thinkingEsc(note) + '</span>';
  html += '<div class="idea-brief-proposal-facts"><span>No task created</span><span>No assignment</span><span>No dispatch</span><span>No accepted decision</span></div>';
  html += '</div>';
  return html;
}

function _ideaBriefSelectedLinkSourceKey(draft) {
  return String((draft && draft.link_source_key) || _ideaBriefDefaultLinkSourceKey(draft));
}

function _renderIdeaBriefLinks(draft) {
  var links = _ideaBriefCopyLinks(draft.thinking_links);
  var group = _thinkingGroup();
  var sources = _ideaBriefLinkSources(group, links);
  var selectedSource = _ideaBriefSelectedLinkSourceKey(draft);
  var html = '<section class="idea-brief-link-section"><div class="idea-brief-section-title"><h3>Linked Thinking</h3><span>Trace the brief back to scratchpad notes.</span></div>';
  html += '<div class="idea-brief-link-list">';
  if (!links.length) html += '<div class="thinking-empty ui-state ui-state--empty ui-state--compact">No linked Thinking yet. Add a Scratchpad note for traceability.</div>';
  links.forEach(function(link, index) {
    var key = _ideaBriefLinkKey(link, index);
    var selected = _ideaBriefSelectedLinkKey === key;
    html += '<article class="idea-brief-link-card' + (selected ? ' selected' : '') + '">';
    html += '<button type="button" class="idea-brief-link-main" onclick="ideaBriefSelectLink(\'' + _thinkingJs(key) + '\')">';
    html += '<span class="idea-brief-link-type">' + _thinkingEsc(_ideaBriefLinkSubtitle(link)) + '</span>';
    html += '<span class="idea-brief-link-title">' + _thinkingEsc(_ideaBriefLinkTitle(link)) + '</span>';
    html += '<span class="idea-brief-link-id">' + _thinkingEsc(link.id || '') + '</span>';
    html += '</button>';
    var metadata = _ideaBriefLinkMetadata(link);
    if (metadata.length) {
      html += '<div class="idea-brief-link-metadata">';
      metadata.forEach(function(item) {
        html += '<span><strong>' + _thinkingEsc(item.key.replace(/_/g, ' ')) + '</strong> ' + _thinkingEsc(item.value) + '</span>';
      });
      html += '</div>';
    }
    html += '<div class="idea-brief-link-actions"><button type="button" class="btn-secondary" onclick="ideaBriefOpenThinkingLink(' + _thinkingAttr(index) + ')">Open in Thinking</button><button type="button" class="btn-secondary danger" onclick="ideaBriefRemoveLink(' + _thinkingAttr(index) + ')">Remove</button></div>';
    html += '</article>';
  });
  html += '</div>';
  html += '<div class="idea-brief-link-add thinking-form">';
  html += '<label for="idea-brief-link-source">Add Thinking link</label><select id="idea-brief-link-source" data-thinking-field="idea-brief:' + _thinkingAttr(_ideaBriefSelectedId) + ':link-source" onchange="ideaBriefChanged()">';
  if (!sources.length) {
    html += '<option value="">No available Thinking artifacts loaded</option>';
  } else {
    sources.forEach(function(source) {
      html += '<option value="' + _thinkingAttr(source.key) + '"' + (selectedSource === source.key ? ' selected' : '') + '>' + _thinkingEsc(source.label) + '</option>';
    });
  }
  html += '</select>';
  html += '<label for="idea-brief-link-context">Why this link matters</label><textarea id="idea-brief-link-context" data-thinking-field="idea-brief:' + _thinkingAttr(_ideaBriefSelectedId) + ':link-context" rows="2" oninput="ideaBriefChanged()" placeholder="Optional context preserved with the link">' + _thinkingEsc(draft.link_context || '') + '</textarea>';
  html += '<div class="thinking-tool-actions"><button type="button" class="btn-primary" onclick="ideaBriefAddLink()"' + (!sources.length ? ' disabled' : '') + '>Add link</button></div>';
  html += '</div></section>';
  return html;
}

function _renderIdeaBriefDetail() {
  var briefId = String(_ideaBriefSelectedId || '');
  if (!briefId) {
    return '<section class="thinking-editor idea-brief-detail thinking-editor-empty"><div class="thinking-empty-detail ui-state ui-state--empty ui-state--fill">Select or create an Idea Brief. Briefs are proposal-only review artifacts; they do not create tasks or authorize execution.</div></section>';
  }
  var isNew = briefId === THINKING_NEW_IDEA_BRIEF_ID;
  var brief = isNew ? null : (state.idea_briefs && state.idea_briefs[briefId]);
  if (!isNew && (!brief || !_ideaBriefIsVisible(brief, _ideaBriefIncludeArchived))) {
    return '<section class="thinking-editor idea-brief-detail thinking-editor-empty"><div class="thinking-empty-detail ui-state ui-state--note ui-state--fill">This Idea Brief is not visible in the current list.</div></section>';
  }
  if (!isNew && _ideaBriefNeedsDetail(briefId)) {
    return '<section class="thinking-editor idea-brief-detail thinking-editor-empty" id="thinking-idea-detail"><div class="thinking-loading ui-state ui-state--loading ui-state--fill" role="status" aria-live="polite">Loading the full Idea Brief before it can be edited…</div></section>';
  }
  var draft = _ideaBriefDraft(briefId);
  var dirty = _ideaBriefDraftIsDirty(briefId, draft);
  var status = isNew ? 'draft' : _ideaBriefStatus(brief);
  var proposalResult = !isNew ? _ideaBriefProposalResultById[briefId] : null;
  var title = isNew ? 'New Idea Brief' : (brief.title || briefId);
  var disabledLifecycle = isNew || status === 'archived' || _ideaBriefSaving;
  var renderedDraft = _ideaBriefDraftSnapshot(draft);
  renderedDraft.link_source_key = _ideaBriefSelectedLinkSourceKey(draft);
  _ideaBriefRenderedDraftsById[briefId] = renderedDraft;
  var html = '<section class="thinking-editor idea-brief-detail" id="thinking-idea-detail">';
  html += '<div class="thinking-detail-head"><div><div class="thinking-kicker">Idea Brief</div><h2>' + _thinkingEsc(title || 'Idea Brief') + '</h2><div class="thinking-detail-id">' + _thinkingEsc(isNew ? 'Draft' : briefId) + ' · <span class="idea-brief-status-pill idea-brief-status-' + _thinkingAttr(status) + '">' + _thinkingEsc(_ideaBriefStatusLabel(status)) + '</span></div></div>';
  if (!isNew) html += '<button type="button" class="thinking-close" onclick="ideaBriefSelect(\'\')" title="Close" aria-label="Close idea brief details">×</button>';
  html += '</div>';
  html += _renderIdeaBriefProposalBanner(brief, proposalResult);
  html += '<div class="idea-brief-review-banner"><strong>Review artifact</strong><span>Use this to evaluate an opportunity, refine the proposal, park it, archive it, or propose it for product-safe review. It never auto-dispatches or auto-assigns work.</span></div>';
  html += '<div class="thinking-form idea-brief-form">';
  IDEA_BRIEF_FIELDS.forEach(function(field) { html += _renderIdeaBriefField(field, draft); });
  html += '</div>';
  html += _renderIdeaBriefLinks(draft);
  html += '<section class="idea-brief-review-actions thinking-form"><div class="idea-brief-section-title"><h3>Next step</h3><span>Choose a review path without converting this into execution.</span></div>';
  if (!isNew) {
    html += '<label for="idea-brief-refinement-note">Refinement note</label><textarea id="idea-brief-refinement-note" data-thinking-field="idea-brief:' + _thinkingAttr(briefId) + ':refinement-note" rows="2" oninput="ideaBriefChanged()" placeholder="What changed or what should Catalyst refine?">' + _thinkingEsc(draft.refinement_note || '') + '</textarea>';
    html += '<label for="idea-brief-proposal-note">Proposal review note</label><textarea id="idea-brief-proposal-note" data-thinking-field="idea-brief:' + _thinkingAttr(briefId) + ':proposal-note" rows="2" oninput="ideaBriefChanged()" placeholder="Optional note for Blueprint/Torqly/user review">' + _thinkingEsc(draft.proposal_note || '') + '</textarea>';
    html += '<label for="idea-brief-lifecycle-reason">Park/archive reason</label><textarea id="idea-brief-lifecycle-reason" data-thinking-field="idea-brief:' + _thinkingAttr(briefId) + ':lifecycle-reason" rows="2" oninput="ideaBriefChanged()" placeholder="Optional reason for parking or archiving">' + _thinkingEsc(draft.lifecycle_reason || '') + '</textarea>';
  }
  html += '</section>';
  html += '<div class="thinking-detail-actions idea-brief-actions"><span class="thinking-save-status" id="idea-brief-draft-status">' + _thinkingEsc(isNew ? 'Unsaved Idea Brief' : (dirty ? 'Unsaved Idea Brief edits' : 'Saved Idea Brief')) + '</span>';
  if (!isNew) {
    html += '<button type="button" class="btn-secondary" onclick="ideaBriefRefine()"' + (disabledLifecycle ? ' disabled' : '') + '>Refine</button>';
    html += '<button type="button" class="btn-secondary" onclick="ideaBriefPark()"' + (disabledLifecycle ? ' disabled' : '') + '>Park</button>';
    html += '<button type="button" class="btn-secondary danger" onclick="ideaBriefArchive()"' + (disabledLifecycle ? ' disabled' : '') + '>Archive</button>';
    html += '<button type="button" class="btn-primary idea-brief-propose-btn" onclick="ideaBriefPropose()"' + (disabledLifecycle ? ' disabled' : '') + '>Propose for review</button>';
  }
  html += '<button type="button" class="btn-primary" onclick="ideaBriefSave()"' + (_ideaBriefSaving ? ' disabled' : '') + '>' + (_ideaBriefSaving ? 'Saving…' : (isNew ? 'Create brief' : 'Save edits')) + '</button>';
  html += '</div>';
  html += '</section>';
  return html;
}

function _renderThinkingIdeaBriefs(group) {
  ideaBriefEnsureLoaded();
  thinkingEnsureScratchpadLoaded();
  return '<div class="thinking-workspace idea-brief-workspace" id="thinking-workspace">'
    + _renderIdeaBriefList(group) + _renderIdeaBriefDetail() + '</div>';
}
function renderThinkingPanel() {
  var panel = document.getElementById('panel-thinking');
  if (!panel) return;
  _thinkingEnsureState();
  _thinkingCaptureDrafts();
  var snapshot = typeof _captureSurfaceState === 'function' ? _captureSurfaceState(panel, _ideaBriefSurfaceStateOptions()) : null;
  var group = _thinkingGroup();
  var notes = _thinkingNotesForGroup(group);
  var briefs = _ideaBriefsForGroup(group, _ideaBriefIncludeArchived);
  var activeTab = String(_thinkingActiveTab || 'scratchpad');
  var html = '<div class="thinking-panel">';
  html += '<div class="tpled-header thinking-header ui-panel-header ui-panel-header--surface"><div class="tpled-header-copy ui-panel-header__copy"><div class="tpled-header-title-row ui-panel-header__title-row"><span class="tpled-header-title ui-panel-header__title">Thinking</span></div>';
  html += '<div class="tpled-header-subtitle ui-panel-header__subtitle">Scratchpad notes and Idea Briefs are group-scoped thinking tools for ' + _thinkingEsc(group || 'all groups') + '; they stay separate from Planning execution.</div></div>';
  html += '<div class="tpled-header-controls ui-panel-header__actions"><span class="thinking-total ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">' + _thinkingEsc(notes.length + ' notes · ' + briefs.length + ' briefs') + '</span><button class="tpled-new-btn" onclick="thinkingRefresh()" title="Refresh Thinking data" aria-label="Refresh Thinking data">&#x21BB;</button></div></div>';
  html += _renderThinkingTabs(notes.length, briefs.length);
  if (_thinkingLastError) html += '<div class="thinking-error ui-state ui-state--error ui-state--compact" role="alert">' + _thinkingEsc(_thinkingLastError) + ' Refresh Thinking to try again.</div>';
  if (_ideaBriefLastError && activeTab === 'idea-briefs') html += '<div class="thinking-error ui-state ui-state--error ui-state--compact" role="alert">' + _thinkingEsc(_ideaBriefLastError) + ' Refresh Thinking to try again.</div>';
  if (_ideaBriefLastStatus && activeTab === 'idea-briefs') html += '<div class="idea-brief-status-message">' + _thinkingEsc(_ideaBriefLastStatus) + '</div>';
  html += activeTab === 'idea-briefs' ? _renderThinkingIdeaBriefs(group) : _renderThinkingScratchpad(group);
  html += '</div>';
  panel.innerHTML = html;
  if (typeof _restoreSurfaceState === 'function') {
    var pending = _ideaBriefPendingDetailSurfaceState;
    var restoreSnapshot = snapshot;
    if (pending && pending.briefId === _ideaBriefSelectedId && !_ideaBriefNeedsDetail(pending.briefId)) {
      restoreSnapshot = pending.snapshot;
      _ideaBriefPendingDetailSurfaceState = null;
    }
    _restoreSurfaceState(panel, restoreSnapshot, _ideaBriefSurfaceStateOptions());
  }
}

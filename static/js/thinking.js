/* ------------------------------------------------------------------ */
/* Thinking panel — Scratchpad + Mind Map                              */
/* ------------------------------------------------------------------ */

var THINKING_TABS = ['scratchpad', 'mind-map'];
var THINKING_NEW_NOTE_ID = '__new_note__';
var THINKING_NEW_MAP_ID = '__new_map__';

var _thinkingStateRef = null;
var _thinkingActiveTab = 'scratchpad';
var _thinkingScratchLoadedGroup = null;
var _thinkingScratchLoadingGroup = null;
var _thinkingScratchSelectedId = '';
var _thinkingScratchDraftsById = {};
var _thinkingMindLoadedGroup = null;
var _thinkingMindLoadingGroup = null;
var _thinkingMindSelectedId = '';
var _thinkingMindDetailLoadingId = '';
var _thinkingMindDetailsById = {};
var _thinkingMapDraftsById = {};
var _thinkingNodeDraftsByMap = {};
var _thinkingLinkDraftsByMap = {};
var _thinkingSelectedNodeId = '';
var _thinkingSelectedLinkId = '';
var _thinkingNodeEditDraftsById = {};
var _thinkingLinkEditDraftsById = {};
var _thinkingSaving = false;
var _thinkingLastError = '';
var _thinkingLastCommandAt = 0;
var _thinkingDrag = null;

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

function _thinkingConnectedNodesIcon(className) {
  var cls = 'thinking-icon';
  if (className) cls += ' ' + String(className);
  return '<span class="' + cls + '" aria-hidden="true">'
    + '<svg class="thinking-connected-nodes-icon" viewBox="0 0 16 16" focusable="false">'
    + '<path d="M7.15 5.55 4.9 9.65M8.85 5.55l2.25 4.1M5.8 11.4h4.4"></path>'
    + '<circle cx="8" cy="4" r="1.8"></circle>'
    + '<circle cx="4" cy="11.4" r="1.8"></circle>'
    + '<circle cx="12" cy="11.4" r="1.8"></circle>'
    + '</svg></span>';
}

function _thinkingSyncStateReference() {
  if (typeof state === 'undefined' || !state) return;
  if (_thinkingStateRef === state) return;
  _thinkingStateRef = state;
  _thinkingScratchLoadedGroup = null;
  _thinkingScratchLoadingGroup = null;
  _thinkingMindLoadedGroup = null;
  _thinkingMindLoadingGroup = null;
  _thinkingMindDetailLoadingId = '';
  _thinkingMindDetailsById = {};
}

function _thinkingEnsureState() {
  _thinkingSyncStateReference();
  if (typeof state === 'undefined' || !state) {
    if (typeof globalThis !== 'undefined') globalThis.state = {};
  }
  if (!state.thinking || typeof state.thinking !== 'object') {
    state.thinking = { scratchpad_notes: {}, mind_maps: {} };
  }
  if (!state.thinking.scratchpad_notes || typeof state.thinking.scratchpad_notes !== 'object') {
    state.thinking.scratchpad_notes = {};
  }
  if (!state.thinking.mind_maps || typeof state.thinking.mind_maps !== 'object') {
    state.thinking.mind_maps = {};
  }
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

function _thinkingMapsForGroup(group) {
  _thinkingEnsureState();
  var items = [];
  var g = String(group || '');
  var maps = state.thinking.mind_maps || {};
  for (var id in maps) {
    var item = maps[id];
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

function thinkingEnsureMindMapsLoaded(opts) {
  opts = opts || {};
  var group = _thinkingGroup();
  if (!group && !opts.force) return false;
  if (!opts.force && (_thinkingMindLoadedGroup === group || _thinkingMindLoadingGroup === group)) return false;
  _thinkingMindLoadingGroup = group;
  _thinkingLastError = '';
  if (typeof send === 'function') send({ cmd: 'mind_map_list', group: group, include_archived: false, include_deleted: false });
  return true;
}

function thinkingEnsureLoaded(opts) {
  opts = opts || {};
  var loaded = false;
  if (_thinkingActiveTab === 'mind-map') {
    loaded = thinkingEnsureMindMapsLoaded(opts) || loaded;
    if (opts.includeInactive || opts.all) loaded = thinkingEnsureScratchpadLoaded(opts) || loaded;
  } else {
    loaded = thinkingEnsureScratchpadLoaded(opts) || loaded;
    if (opts.includeInactive || opts.all) loaded = thinkingEnsureMindMapsLoaded(opts) || loaded;
  }
  return loaded;
}

function thinkingRefresh() {
  _thinkingCaptureDrafts();
  thinkingEnsureScratchpadLoaded({ force: true });
  thinkingEnsureMindMapsLoaded({ force: true });
  if (_thinkingMindSelectedId && _thinkingMindSelectedId !== THINKING_NEW_MAP_ID) {
    thinkingMindLoadDetail(_thinkingMindSelectedId, { force: true });
  }
  if (typeof renderThinkingPanel === 'function') renderThinkingPanel();
}

function thinkingBeginGroupSwitch() {
  _thinkingCaptureDrafts();
  var group = _thinkingGroup() || '';
  var note = _thinkingScratchSelectedId && state && state.thinking && state.thinking.scratchpad_notes
    ? state.thinking.scratchpad_notes[_thinkingScratchSelectedId]
    : null;
  if (_thinkingScratchSelectedId !== THINKING_NEW_NOTE_ID && note && _thinkingItemGroup(note) !== group) {
    _thinkingScratchSelectedId = '';
  }
  var map = _thinkingMindSelectedId && state && state.thinking && state.thinking.mind_maps
    ? state.thinking.mind_maps[_thinkingMindSelectedId]
    : null;
  if (_thinkingMindSelectedId !== THINKING_NEW_MAP_ID && map && _thinkingItemGroup(map) !== group) {
    _thinkingMindSelectedId = '';
    _thinkingSelectedNodeId = '';
    _thinkingSelectedLinkId = '';
  }
  thinkingEnsureScratchpadLoaded({ force: true });
  thinkingEnsureMindMapsLoaded({ force: true });
  renderThinkingPanel();
}

function _thinkingFirstActiveNoteId(group) {
  var notes = _thinkingNotesForGroup(group);
  return notes.length ? String(notes[0].id || '') : '';
}

function _thinkingFirstActiveMapId(group) {
  var maps = _thinkingMapsForGroup(group);
  return maps.length ? String(maps[0].id || '') : '';
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

function thinkingReceiveMindMapList(msg) {
  _thinkingEnsureState();
  var group = String((msg && msg.group) || _thinkingGroup() || '');
  var incoming = Array.isArray(msg && msg.mind_maps) ? msg.mind_maps : [];
  for (var id in state.thinking.mind_maps) {
    var existing = state.thinking.mind_maps[id];
    if (existing && _thinkingItemGroup(existing) === group) delete state.thinking.mind_maps[id];
  }
  incoming.forEach(function(mindMap) {
    if (mindMap && mindMap.id) state.thinking.mind_maps[mindMap.id] = Object.assign({}, mindMap);
  });
  if (_thinkingMindLoadingGroup === group) _thinkingMindLoadingGroup = null;
  _thinkingMindLoadedGroup = group;
  if (_thinkingMindSelectedId && _thinkingMindSelectedId !== THINKING_NEW_MAP_ID) {
    var selected = state.thinking.mind_maps[_thinkingMindSelectedId];
    if (!selected || !_thinkingIsActive(selected) || _thinkingItemGroup(selected) !== group) {
      _thinkingMindSelectedId = _thinkingFirstActiveMapId(group);
      _thinkingSelectedNodeId = '';
      _thinkingSelectedLinkId = '';
    }
  }
  if (_thinkingPanelVisible()) renderThinkingPanel();
}

function _thinkingNormalizeMindMapPayload(msg) {
  if (!msg) return null;
  if (msg.mind_map) return msg.mind_map;
  if (msg.id && (msg.type === 'mind_map' || msg.nodes || msg.links)) {
    var payload = Object.assign({}, msg);
    delete payload.type;
    return payload;
  }
  return null;
}

function _thinkingStoreMindMapDetail(payload) {
  if (!payload || !payload.id) return null;
  _thinkingEnsureState();
  var mapId = String(payload.id || '');
  var summary = Object.assign({}, payload);
  delete summary.nodes;
  delete summary.links;
  state.thinking.mind_maps[mapId] = Object.assign({}, state.thinking.mind_maps[mapId] || {}, summary);
  _thinkingMindDetailsById[mapId] = Object.assign({}, _thinkingMindDetailsById[mapId] || {}, payload, {
    nodes: Array.isArray(payload.nodes) ? payload.nodes.slice() : ((_thinkingMindDetailsById[mapId] && _thinkingMindDetailsById[mapId].nodes) || []),
    links: Array.isArray(payload.links) ? payload.links.slice() : ((_thinkingMindDetailsById[mapId] && _thinkingMindDetailsById[mapId].links) || []),
  });
  return _thinkingMindDetailsById[mapId];
}

function thinkingReceiveMindMapDetail(msg) {
  var payload = _thinkingNormalizeMindMapPayload(msg);
  if (!payload || !payload.id) return;
  _thinkingStoreMindMapDetail(payload);
  _thinkingMindDetailLoadingId = '';
  _thinkingSaving = false;
  _thinkingLastError = '';
  if (!_thinkingMindSelectedId || _thinkingMindSelectedId === THINKING_NEW_MAP_ID) _thinkingMindSelectedId = payload.id;
  if (_thinkingPanelVisible()) renderThinkingPanel();
}

function thinkingReceiveMindMapMutation(msg) {
  var mindMap = msg && msg.mind_map ? msg.mind_map : null;
  if (!mindMap || !mindMap.id) return;
  _thinkingEnsureState();
  state.thinking.mind_maps[mindMap.id] = Object.assign({}, state.thinking.mind_maps[mindMap.id] || {}, mindMap);
  if (_thinkingMindDetailsById[mindMap.id]) {
    _thinkingMindDetailsById[mindMap.id] = Object.assign({}, _thinkingMindDetailsById[mindMap.id], mindMap);
  }
  _thinkingSaving = false;
  _thinkingLastError = '';
  if (msg.type === 'mind_map_created') {
    delete _thinkingMapDraftsById[THINKING_NEW_MAP_ID];
    _thinkingMindSelectedId = mindMap.id;
    _thinkingSelectedNodeId = '';
    _thinkingSelectedLinkId = '';
    thinkingMindLoadDetail(mindMap.id, { force: true });
  } else if (!_thinkingIsActive(mindMap) && _thinkingMindSelectedId === mindMap.id) {
    delete _thinkingMapDraftsById[mindMap.id];
    delete _thinkingMindDetailsById[mindMap.id];
    _thinkingMindSelectedId = _thinkingFirstActiveMapId(_thinkingGroup());
    _thinkingSelectedNodeId = '';
    _thinkingSelectedLinkId = '';
  } else if (_thinkingMindSelectedId === mindMap.id) {
    delete _thinkingMapDraftsById[mindMap.id];
    thinkingMindLoadDetail(mindMap.id, { force: true });
  }
  thinkingEnsureMindMapsLoaded({ force: true });
  if (_thinkingPanelVisible()) renderThinkingPanel();
}

function thinkingReceiveMindMapDelta(mindMap) {
  if (!mindMap || !mindMap.id) return;
  _thinkingEnsureState();
  state.thinking.mind_maps[mindMap.id] = Object.assign({}, state.thinking.mind_maps[mindMap.id] || {}, mindMap);
  if (_thinkingMindDetailsById[mindMap.id]) {
    _thinkingMindDetailsById[mindMap.id] = Object.assign({}, _thinkingMindDetailsById[mindMap.id], mindMap);
  }
  if (!_thinkingIsActive(mindMap) && _thinkingMindSelectedId === mindMap.id) {
    _thinkingMindSelectedId = _thinkingFirstActiveMapId(_thinkingGroup());
    _thinkingSelectedNodeId = '';
    _thinkingSelectedLinkId = '';
  }
}

function _thinkingDetailForNode(node) {
  if (!node || !node.map_id) return null;
  return _thinkingMindDetailsById[String(node.map_id || '')] || null;
}

function _thinkingUpsertById(items, item) {
  var out = Array.isArray(items) ? items.slice() : [];
  var found = false;
  for (var i = 0; i < out.length; i++) {
    if (String(out[i].id || '') === String(item.id || '')) {
      out[i] = Object.assign({}, out[i], item);
      found = true;
      break;
    }
  }
  if (!found) out.push(Object.assign({}, item));
  return out;
}

function _thinkingSortByOrder(items) {
  return (Array.isArray(items) ? items.slice() : []).sort(function(a, b) {
    var ao = Number(a && a.sort_order);
    var bo = Number(b && b.sort_order);
    if (!Number.isFinite(ao)) ao = 0;
    if (!Number.isFinite(bo)) bo = 0;
    if (ao !== bo) return ao - bo;
    return String((a && (a.label || a.title || a.id)) || '').localeCompare(String((b && (b.label || b.title || b.id)) || ''));
  });
}

function thinkingReceiveMindMapNodeMutation(msg) {
  var node = msg && msg.node ? msg.node : null;
  if (!node || !node.id) return;
  thinkingReceiveMindMapNodeDelta(node);
  _thinkingSaving = false;
  _thinkingLastError = '';
  if (msg.type === 'mind_map_node_created') {
    var mapId = String(node.map_id || _thinkingMindSelectedId || '');
    delete _thinkingNodeDraftsByMap[mapId];
    _thinkingSelectedNodeId = node.id;
    _thinkingSelectedLinkId = '';
  } else if (node.deleted && _thinkingSelectedNodeId === node.id) {
    _thinkingSelectedNodeId = '';
  } else if (_thinkingSelectedNodeId === node.id) {
    delete _thinkingNodeEditDraftsById[node.id];
  }
  if (_thinkingPanelVisible()) renderThinkingPanel();
}

function thinkingReceiveMindMapNodeDelta(node) {
  if (!node || !node.id) return;
  var mapId = String(node.map_id || _thinkingMindSelectedId || '');
  if (!mapId) return;
  var detail = _thinkingMindDetailsById[mapId];
  if (!detail) return;
  detail.nodes = _thinkingSortByOrder(_thinkingUpsertById(detail.nodes, node));
  detail.node_count = detail.nodes.filter(function(item) { return item && !item.deleted; }).length;
  if (node.deleted && _thinkingSelectedNodeId === node.id) _thinkingSelectedNodeId = '';
}

function thinkingReceiveMindMapNodeReordered(msg) {
  var mapId = String((msg && msg.map_id) || _thinkingMindSelectedId || '');
  if (!mapId || !_thinkingMindDetailsById[mapId]) return;
  var nodes = Array.isArray(msg && msg.nodes) ? msg.nodes : [];
  for (var i = 0; i < nodes.length; i++) thinkingReceiveMindMapNodeDelta(nodes[i]);
  _thinkingSaving = false;
  if (_thinkingPanelVisible()) renderThinkingPanel();
}

function thinkingReceiveMindMapLinkMutation(msg) {
  var link = msg && msg.link ? msg.link : null;
  if (!link || !link.id) return;
  thinkingReceiveMindMapLinkDelta(link);
  _thinkingSaving = false;
  _thinkingLastError = '';
  if (msg.type === 'mind_map_link_created') {
    var mapId = String(link.map_id || _thinkingMindSelectedId || '');
    delete _thinkingLinkDraftsByMap[mapId];
    _thinkingSelectedLinkId = link.id;
    _thinkingSelectedNodeId = '';
  } else if (link.deleted && _thinkingSelectedLinkId === link.id) {
    _thinkingSelectedLinkId = '';
  } else if (_thinkingSelectedLinkId === link.id) {
    delete _thinkingLinkEditDraftsById[link.id];
  }
  if (_thinkingPanelVisible()) renderThinkingPanel();
}

function thinkingReceiveMindMapLinkDelta(link) {
  if (!link || !link.id) return;
  var mapId = String(link.map_id || _thinkingMindSelectedId || '');
  if (!mapId) return;
  var detail = _thinkingMindDetailsById[mapId];
  if (!detail) return;
  detail.links = _thinkingSortByOrder(_thinkingUpsertById(detail.links, link));
  detail.link_count = detail.links.filter(function(item) { return item && !item.deleted; }).length;
  if (link.deleted && _thinkingSelectedLinkId === link.id) _thinkingSelectedLinkId = '';
}

function thinkingReceiveMindMapLinkReordered(msg) {
  var mapId = String((msg && msg.map_id) || _thinkingMindSelectedId || '');
  if (!mapId || !_thinkingMindDetailsById[mapId]) return;
  var links = Array.isArray(msg && msg.links) ? msg.links : [];
  for (var i = 0; i < links.length; i++) thinkingReceiveMindMapLinkDelta(links[i]);
  _thinkingSaving = false;
  if (_thinkingPanelVisible()) renderThinkingPanel();
}

function thinkingHandleError(msg) {
  if (!_thinkingPanelVisible()) return false;
  var age = _thinkingNowMs() - Number(_thinkingLastCommandAt || 0);
  var likelyThinking = age >= 0 && age < 15000;
  var text = String((msg && msg.message) || 'Thinking command failed.');
  if (!likelyThinking && !/Scratchpad|Mind Map|mind map|scratchpad|thinking/i.test(text)) return false;
  _thinkingSaving = false;
  _thinkingScratchLoadingGroup = null;
  _thinkingMindLoadingGroup = null;
  _thinkingMindDetailLoadingId = '';
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

function _thinkingCaptureMapDraft() {
  if (!_thinkingMindSelectedId) return;
  var titleEl = _thinkingEl('thinking-map-title');
  var descEl = _thinkingEl('thinking-map-description');
  if (!titleEl && !descEl) return;
  _thinkingMapDraftsById[_thinkingMindSelectedId] = {
    title: titleEl && 'value' in titleEl ? String(titleEl.value || '') : '',
    description: descEl && 'value' in descEl ? String(descEl.value || '') : '',
  };
}

function _thinkingCaptureNodeDraft() {
  var mapId = String(_thinkingMindSelectedId || '');
  if (!mapId || mapId === THINKING_NEW_MAP_ID) return;
  var labelEl = _thinkingEl('thinking-node-new-label');
  var notesEl = _thinkingEl('thinking-node-new-notes');
  var typeEl = _thinkingEl('thinking-node-new-type');
  var colorEl = _thinkingEl('thinking-node-new-color');
  if (labelEl || notesEl || typeEl || colorEl) {
    _thinkingNodeDraftsByMap[mapId] = {
      label: labelEl && 'value' in labelEl ? String(labelEl.value || '') : '',
      notes: notesEl && 'value' in notesEl ? String(notesEl.value || '') : '',
      node_type: typeEl && 'value' in typeEl ? String(typeEl.value || '') : '',
      color: colorEl && 'value' in colorEl ? String(colorEl.value || '') : '',
    };
  }
  if (!_thinkingSelectedNodeId) return;
  var editLabel = _thinkingEl('thinking-node-edit-label');
  var editNotes = _thinkingEl('thinking-node-edit-notes');
  var editType = _thinkingEl('thinking-node-edit-type');
  var editColor = _thinkingEl('thinking-node-edit-color');
  if (editLabel || editNotes || editType || editColor) {
    _thinkingNodeEditDraftsById[_thinkingSelectedNodeId] = {
      label: editLabel && 'value' in editLabel ? String(editLabel.value || '') : '',
      notes: editNotes && 'value' in editNotes ? String(editNotes.value || '') : '',
      node_type: editType && 'value' in editType ? String(editType.value || '') : '',
      color: editColor && 'value' in editColor ? String(editColor.value || '') : '',
    };
  }
}

function _thinkingCaptureLinkDraft() {
  var mapId = String(_thinkingMindSelectedId || '');
  if (!mapId || mapId === THINKING_NEW_MAP_ID) return;
  var sourceEl = _thinkingEl('thinking-link-new-source');
  var targetEl = _thinkingEl('thinking-link-new-target');
  var labelEl = _thinkingEl('thinking-link-new-label');
  var typeEl = _thinkingEl('thinking-link-new-type');
  if (sourceEl || targetEl || labelEl || typeEl) {
    _thinkingLinkDraftsByMap[mapId] = {
      source_node_id: sourceEl && 'value' in sourceEl ? String(sourceEl.value || '') : '',
      target_node_id: targetEl && 'value' in targetEl ? String(targetEl.value || '') : '',
      label: labelEl && 'value' in labelEl ? String(labelEl.value || '') : '',
      link_type: typeEl && 'value' in typeEl ? String(typeEl.value || '') : '',
    };
  }
  if (!_thinkingSelectedLinkId) return;
  var editSource = _thinkingEl('thinking-link-edit-source');
  var editTarget = _thinkingEl('thinking-link-edit-target');
  var editLabel = _thinkingEl('thinking-link-edit-label');
  var editType = _thinkingEl('thinking-link-edit-type');
  if (editSource || editTarget || editLabel || editType) {
    _thinkingLinkEditDraftsById[_thinkingSelectedLinkId] = {
      source_node_id: editSource && 'value' in editSource ? String(editSource.value || '') : '',
      target_node_id: editTarget && 'value' in editTarget ? String(editTarget.value || '') : '',
      label: editLabel && 'value' in editLabel ? String(editLabel.value || '') : '',
      link_type: editType && 'value' in editType ? String(editType.value || '') : '',
    };
  }
}

function _thinkingCaptureDrafts() {
  _thinkingCaptureScratchDraft();
  _thinkingCaptureMapDraft();
  _thinkingCaptureNodeDraft();
  _thinkingCaptureLinkDraft();
}

function thinkingSetTab(tab) {
  tab = String(tab || 'scratchpad');
  if (THINKING_TABS.indexOf(tab) < 0) tab = 'scratchpad';
  _thinkingCaptureDrafts();
  _thinkingActiveTab = tab;
  if (tab === 'mind-map') thinkingEnsureMindMapsLoaded();
  else thinkingEnsureScratchpadLoaded();
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

function thinkingMindNew() {
  _thinkingCaptureDrafts();
  _thinkingMindSelectedId = THINKING_NEW_MAP_ID;
  _thinkingSelectedNodeId = '';
  _thinkingSelectedLinkId = '';
  if (!_thinkingMapDraftsById[THINKING_NEW_MAP_ID]) {
    _thinkingMapDraftsById[THINKING_NEW_MAP_ID] = { title: '', description: '' };
  }
  renderThinkingPanel();
}

function thinkingMindSelect(mapId) {
  _thinkingCaptureDrafts();
  _thinkingMindSelectedId = String(mapId || '');
  _thinkingSelectedNodeId = '';
  _thinkingSelectedLinkId = '';
  if (_thinkingMindSelectedId && _thinkingMindSelectedId !== THINKING_NEW_MAP_ID) {
    thinkingMindLoadDetail(_thinkingMindSelectedId);
  }
  renderThinkingPanel();
}

function thinkingMindLoadDetail(mapId, opts) {
  opts = opts || {};
  mapId = String(mapId || _thinkingMindSelectedId || '');
  if (!mapId || mapId === THINKING_NEW_MAP_ID) return false;
  if (!opts.force && (_thinkingMindDetailsById[mapId] || _thinkingMindDetailLoadingId === mapId)) return false;
  _thinkingMindDetailLoadingId = mapId;
  if (typeof send === 'function') send({ cmd: 'mind_map_show', group: _thinkingGroup(), mind_map_id: mapId });
  return true;
}

function thinkingMindChanged() {
  _thinkingCaptureMapDraft();
  _thinkingSetText('thinking-map-draft-status', 'Unsaved map edits');
}

function _thinkingMapDraft(mapId) {
  var draft = _thinkingMapDraftsById[mapId];
  if (draft) return Object.assign({}, draft);
  var map = state && state.thinking && state.thinking.mind_maps
    ? state.thinking.mind_maps[mapId]
    : null;
  return {
    title: map ? String(map.title || '') : '',
    description: map ? String(map.description || '') : '',
  };
}

function _thinkingMapIsDirty(mapId, draft) {
  if (mapId === THINKING_NEW_MAP_ID) return !!(draft.title || draft.description);
  var map = state && state.thinking && state.thinking.mind_maps
    ? state.thinking.mind_maps[mapId]
    : null;
  if (!map) return false;
  return String(map.title || '') !== String(draft.title || '')
    || String(map.description || '') !== String(draft.description || '');
}

function thinkingMindSaveMap() {
  _thinkingCaptureMapDraft();
  var mapId = String(_thinkingMindSelectedId || '');
  if (!mapId) return;
  var draft = _thinkingMapDraft(mapId);
  var title = String(draft.title || '').trim() || 'Untitled mind map';
  if (mapId === THINKING_NEW_MAP_ID) {
    _thinkingSend({
      cmd: 'mind_map_create',
      group: _thinkingGroup(),
      title: title,
      description: draft.description || '',
      metadata: { source: 'thinking_panel', handoff_ready: false },
    });
  } else {
    _thinkingSend({
      cmd: 'mind_map_update',
      group: _thinkingGroup(),
      mind_map_id: mapId,
      title: title,
      description: draft.description || '',
    });
  }
  renderThinkingPanel();
}

function thinkingMindArchive(mapId) {
  mapId = String(mapId || _thinkingMindSelectedId || '');
  if (!mapId || mapId === THINKING_NEW_MAP_ID) return;
  _thinkingConfirm('Archive this Mind Map? It will leave the default Thinking list.', {
    label: 'Archive', variant: 'btn-secondary', title: 'Archive Mind Map'
  }).then(function(ok) {
    if (!ok) return;
    _thinkingSend({ cmd: 'mind_map_archive', group: _thinkingGroup(), mind_map_id: mapId });
    renderThinkingPanel();
  });
}

function thinkingMindDelete(mapId) {
  mapId = String(mapId || _thinkingMindSelectedId || '');
  if (!mapId || mapId === THINKING_NEW_MAP_ID) return;
  _thinkingConfirm('Delete this Mind Map and its nodes/links? This is a soft delete hidden by default.', {
    label: 'Delete', variant: 'btn-danger', title: 'Delete Mind Map'
  }).then(function(ok) {
    if (!ok) return;
    _thinkingSend({ cmd: 'mind_map_delete', group: _thinkingGroup(), mind_map_id: mapId });
    renderThinkingPanel();
  });
}

function _thinkingMapDetail(mapId) {
  return _thinkingMindDetailsById[String(mapId || '')] || null;
}

function _thinkingActiveNodes(detail) {
  return _thinkingSortByOrder(((detail && detail.nodes) || []).filter(function(node) {
    return node && !node.deleted && !String(node.deleted_at || '').trim();
  }));
}

function _thinkingActiveLinks(detail) {
  var nodeIds = {};
  _thinkingActiveNodes(detail).forEach(function(node) { nodeIds[String(node.id || '')] = true; });
  return _thinkingSortByOrder(((detail && detail.links) || []).filter(function(link) {
    return link && !link.deleted && !String(link.deleted_at || '').trim()
      && nodeIds[String(link.source_node_id || '')]
      && nodeIds[String(link.target_node_id || '')];
  }));
}

function _thinkingNodeById(detail, nodeId) {
  var nodes = (detail && detail.nodes) || [];
  nodeId = String(nodeId || '');
  for (var i = 0; i < nodes.length; i++) {
    if (String(nodes[i].id || '') === nodeId) return nodes[i];
  }
  return null;
}

function _thinkingLinkById(detail, linkId) {
  var links = (detail && detail.links) || [];
  linkId = String(linkId || '');
  for (var i = 0; i < links.length; i++) {
    if (String(links[i].id || '') === linkId) return links[i];
  }
  return null;
}

function _thinkingClampPosition(value, fallback) {
  var n = Number(value);
  if (!Number.isFinite(n)) n = Number(fallback);
  if (!Number.isFinite(n)) n = 50;
  return Math.max(4, Math.min(96, n));
}

function _thinkingDefaultNodePosition(index, total) {
  total = Math.max(1, Number(total || 1));
  var angle = (Math.PI * 2 * (index || 0)) / total - Math.PI / 2;
  var radius = total <= 1 ? 0 : 28;
  return {
    x: _thinkingClampPosition(50 + Math.cos(angle) * radius, 50),
    y: _thinkingClampPosition(50 + Math.sin(angle) * radius, 50),
  };
}

function _thinkingNodePosition(node, index, total) {
  var pos = node && node.position && typeof node.position === 'object' ? node.position : {};
  var rawX = node && node.x;
  var rawY = node && node.y;
  var hasExplicit = Number.isFinite(Number(rawX)) || Number.isFinite(Number(rawY))
    || Number.isFinite(Number(pos.x)) || Number.isFinite(Number(pos.y));
  if (!hasExplicit || (Number(rawX || 0) === 0 && Number(rawY || 0) === 0 && !Number.isFinite(Number(pos.x)) && !Number.isFinite(Number(pos.y)))) {
    return _thinkingDefaultNodePosition(index, total);
  }
  var x = Number.isFinite(Number(rawX)) ? Number(rawX) : Number(pos.x);
  var y = Number.isFinite(Number(rawY)) ? Number(rawY) : Number(pos.y);
  return {
    x: _thinkingClampPosition(x, 50),
    y: _thinkingClampPosition(y, 50),
  };
}

function _thinkingPositionMap(detail) {
  var nodes = _thinkingActiveNodes(detail);
  var out = {};
  for (var i = 0; i < nodes.length; i++) {
    out[String(nodes[i].id || '')] = _thinkingNodePosition(nodes[i], i, nodes.length);
  }
  return out;
}

function _thinkingUpdateNodeLocal(nodeId, patch) {
  nodeId = String(nodeId || '');
  var detail = _thinkingMapDetail(_thinkingMindSelectedId);
  if (!detail || !nodeId) return null;
  var nodes = detail.nodes || [];
  for (var i = 0; i < nodes.length; i++) {
    if (String(nodes[i].id || '') === nodeId) {
      nodes[i] = Object.assign({}, nodes[i], patch || {});
      detail.nodes = nodes;
      return nodes[i];
    }
  }
  return null;
}

function thinkingMindSelectNode(nodeId) {
  _thinkingCaptureDrafts();
  _thinkingSelectedNodeId = String(nodeId || '');
  _thinkingSelectedLinkId = '';
  renderThinkingPanel();
}

function thinkingMindSelectLink(linkId) {
  _thinkingCaptureDrafts();
  _thinkingSelectedLinkId = String(linkId || '');
  _thinkingSelectedNodeId = '';
  renderThinkingPanel();
}

function thinkingMindNodeChanged() {
  _thinkingCaptureNodeDraft();
}

function thinkingMindLinkChanged() {
  _thinkingCaptureLinkDraft();
}

function thinkingMindAddNode() {
  _thinkingCaptureNodeDraft();
  var mapId = String(_thinkingMindSelectedId || '');
  if (!mapId || mapId === THINKING_NEW_MAP_ID) return;
  var detail = _thinkingMapDetail(mapId);
  var nodes = _thinkingActiveNodes(detail);
  var draft = _thinkingNodeDraftsByMap[mapId] || {};
  var label = String(draft.label || '').trim();
  if (!label) {
    _thinkingLastError = 'Node label is required.';
    renderThinkingPanel();
    return;
  }
  var pos = _thinkingDefaultNodePosition(nodes.length, nodes.length + 1);
  _thinkingSend({
    cmd: 'mind_map_node_create',
    group: _thinkingGroup(),
    mind_map_id: mapId,
    label: label,
    title: label,
    notes: draft.notes || '',
    node_type: draft.node_type || '',
    color: draft.color || '',
    x: pos.x,
    y: pos.y,
    position: { x: pos.x, y: pos.y },
  });
  renderThinkingPanel();
}

function thinkingMindSaveNode() {
  _thinkingCaptureNodeDraft();
  var nodeId = String(_thinkingSelectedNodeId || '');
  var mapId = String(_thinkingMindSelectedId || '');
  if (!nodeId || !mapId) return;
  var draft = _thinkingNodeEditDraftsById[nodeId] || {};
  var label = String(draft.label || '').trim();
  if (!label) {
    _thinkingLastError = 'Node label is required.';
    renderThinkingPanel();
    return;
  }
  _thinkingSend({
    cmd: 'mind_map_node_update',
    group: _thinkingGroup(),
    mind_map_id: mapId,
    node_id: nodeId,
    label: label,
    title: label,
    notes: draft.notes || '',
    node_type: draft.node_type || '',
    color: draft.color || '',
  });
  renderThinkingPanel();
}

function thinkingMindDeleteNode(nodeId) {
  nodeId = String(nodeId || _thinkingSelectedNodeId || '');
  var mapId = String(_thinkingMindSelectedId || '');
  if (!nodeId || !mapId) return;
  _thinkingConfirm('Delete this node? Links attached to it will be hidden by the backend cascade.', {
    label: 'Delete node', variant: 'btn-danger', title: 'Delete Mind Map node'
  }).then(function(ok) {
    if (!ok) return;
    _thinkingSend({ cmd: 'mind_map_node_delete', group: _thinkingGroup(), mind_map_id: mapId, node_id: nodeId });
    renderThinkingPanel();
  });
}

function thinkingMindMoveNode(nodeId, dx, dy) {
  var detail = _thinkingMapDetail(_thinkingMindSelectedId);
  var node = _thinkingNodeById(detail, nodeId);
  if (!node) return;
  var nodes = _thinkingActiveNodes(detail);
  var index = nodes.findIndex(function(item) { return String(item.id || '') === String(nodeId || ''); });
  var pos = _thinkingNodePosition(node, Math.max(0, index), nodes.length);
  var next = {
    x: _thinkingClampPosition(pos.x + Number(dx || 0), pos.x),
    y: _thinkingClampPosition(pos.y + Number(dy || 0), pos.y),
  };
  _thinkingUpdateNodeLocal(nodeId, { x: next.x, y: next.y, position: next });
  if (typeof renderThinkingPanel === 'function') renderThinkingPanel();
  _thinkingSend({
    cmd: 'mind_map_node_position',
    group: _thinkingGroup(),
    mind_map_id: _thinkingMindSelectedId,
    node_id: nodeId,
    x: next.x,
    y: next.y,
    position: { x: next.x, y: next.y },
  });
}

function thinkingMindNodeKeydown(event, nodeId) {
  if (!event) return;
  var key = String(event.key || '');
  var step = event.shiftKey ? 10 : 5;
  if (key === 'Enter' || key === ' ') {
    if (event.preventDefault) event.preventDefault();
    thinkingMindSelectNode(nodeId);
    return;
  }
  var dx = 0;
  var dy = 0;
  if (key === 'ArrowLeft') dx = -step;
  else if (key === 'ArrowRight') dx = step;
  else if (key === 'ArrowUp') dy = -step;
  else if (key === 'ArrowDown') dy = step;
  else if (key === 'Delete' || key === 'Backspace') {
    if (event.preventDefault) event.preventDefault();
    thinkingMindDeleteNode(nodeId);
    return;
  }
  if (dx || dy) {
    if (event.preventDefault) event.preventDefault();
    thinkingMindMoveNode(nodeId, dx, dy);
  }
}

function _thinkingDragPosition(ev) {
  var drag = _thinkingDrag;
  if (!drag || !drag.rect) return null;
  var width = Number(drag.rect.width || (drag.rect.right - drag.rect.left));
  var height = Number(drag.rect.height || (drag.rect.bottom - drag.rect.top));
  if (!Number.isFinite(width) || width <= 0 || !Number.isFinite(height) || height <= 0) return null;
  return {
    x: _thinkingClampPosition(((ev.clientX - drag.rect.left) / width) * 100, 50),
    y: _thinkingClampPosition(((ev.clientY - drag.rect.top) / height) * 100, 50),
  };
}

function _thinkingPaintMapPositions() {
  if (typeof document === 'undefined' || !document || !document.querySelectorAll) return;
  var detail = _thinkingMapDetail(_thinkingMindSelectedId);
  if (!detail) return;
  var positions = _thinkingPositionMap(detail);
  var nodes = document.querySelectorAll('.thinking-map-node[data-node-id]');
  nodes.forEach(function(el) {
    var id = el && el.dataset ? String(el.dataset.nodeId || '') : '';
    var pos = positions[id];
    if (!pos || !el.style) return;
    el.style.left = pos.x + '%';
    el.style.top = pos.y + '%';
  });
  var lines = document.querySelectorAll('.thinking-map-link-line[data-source-node-id]');
  lines.forEach(function(el) {
    var src = el && el.dataset ? String(el.dataset.sourceNodeId || '') : '';
    var dst = el && el.dataset ? String(el.dataset.targetNodeId || '') : '';
    var a = positions[src];
    var b = positions[dst];
    if (!a || !b || typeof el.setAttribute !== 'function') return;
    el.setAttribute('x1', a.x);
    el.setAttribute('y1', a.y);
    el.setAttribute('x2', b.x);
    el.setAttribute('y2', b.y);
  });
}

function thinkingMindNodePointerDown(ev, nodeId) {
  if (!ev || (typeof ev.button === 'number' && ev.button !== 0)) return;
  if (ev.target && typeof ev.target.closest === 'function' && ev.target.closest('.thinking-node-action')) return;
  var stage = _thinkingEl('thinking-map-canvas-wrap');
  if (!stage || typeof stage.getBoundingClientRect !== 'function') return;
  _thinkingCaptureDrafts();
  _thinkingSelectedNodeId = String(nodeId || '');
  _thinkingSelectedLinkId = '';
  _thinkingDrag = {
    nodeId: String(nodeId || ''),
    mapId: String(_thinkingMindSelectedId || ''),
    rect: stage.getBoundingClientRect(),
    moved: false,
  };
  if (ev.currentTarget && typeof ev.currentTarget.setPointerCapture === 'function' && ev.pointerId != null) {
    try { ev.currentTarget.setPointerCapture(ev.pointerId); } catch (_err) {}
  }
  if (document && document.addEventListener) {
    document.addEventListener('pointermove', thinkingMindNodePointerMove);
    document.addEventListener('pointerup', thinkingMindNodePointerUp);
    document.addEventListener('pointercancel', thinkingMindNodePointerUp);
  }
}

function thinkingMindNodePointerMove(ev) {
  if (!_thinkingDrag) return;
  var pos = _thinkingDragPosition(ev);
  if (!pos) return;
  _thinkingDrag.moved = true;
  _thinkingUpdateNodeLocal(_thinkingDrag.nodeId, { x: pos.x, y: pos.y, position: pos });
  _thinkingPaintMapPositions();
}

function thinkingMindNodePointerUp(ev) {
  var drag = _thinkingDrag;
  if (document && document.removeEventListener) {
    document.removeEventListener('pointermove', thinkingMindNodePointerMove);
    document.removeEventListener('pointerup', thinkingMindNodePointerUp);
    document.removeEventListener('pointercancel', thinkingMindNodePointerUp);
  }
  _thinkingDrag = null;
  if (!drag) return;
  var detail = _thinkingMapDetail(drag.mapId);
  var node = _thinkingNodeById(detail, drag.nodeId);
  if (!drag.moved || !node) {
    thinkingMindSelectNode(drag.nodeId);
    return;
  }
  var nodes = _thinkingActiveNodes(detail);
  var index = nodes.findIndex(function(item) { return String(item.id || '') === String(drag.nodeId || ''); });
  var pos = _thinkingNodePosition(node, Math.max(0, index), nodes.length);
  _thinkingSend({
    cmd: 'mind_map_node_position',
    group: _thinkingGroup(),
    mind_map_id: drag.mapId,
    node_id: drag.nodeId,
    x: pos.x,
    y: pos.y,
    position: { x: pos.x, y: pos.y },
  });
  renderThinkingPanel();
}

function thinkingMindAddLink() {
  _thinkingCaptureLinkDraft();
  var mapId = String(_thinkingMindSelectedId || '');
  if (!mapId || mapId === THINKING_NEW_MAP_ID) return;
  var draft = _thinkingLinkDraftsByMap[mapId] || {};
  var source = String(draft.source_node_id || '').trim();
  var target = String(draft.target_node_id || '').trim();
  if (!source || !target || source === target) {
    _thinkingLastError = 'Choose two different nodes for the link.';
    renderThinkingPanel();
    return;
  }
  _thinkingSend({
    cmd: 'mind_map_link_create',
    group: _thinkingGroup(),
    mind_map_id: mapId,
    source_node_id: source,
    target_node_id: target,
    label: draft.label || '',
    link_type: draft.link_type || '',
  });
  renderThinkingPanel();
}

function thinkingMindSaveLink() {
  _thinkingCaptureLinkDraft();
  var linkId = String(_thinkingSelectedLinkId || '');
  var mapId = String(_thinkingMindSelectedId || '');
  if (!linkId || !mapId) return;
  var draft = _thinkingLinkEditDraftsById[linkId] || {};
  var source = String(draft.source_node_id || '').trim();
  var target = String(draft.target_node_id || '').trim();
  if (!source || !target || source === target) {
    _thinkingLastError = 'Choose two different nodes for the link.';
    renderThinkingPanel();
    return;
  }
  _thinkingSend({
    cmd: 'mind_map_link_update',
    group: _thinkingGroup(),
    mind_map_id: mapId,
    link_id: linkId,
    source_node_id: source,
    target_node_id: target,
    label: draft.label || '',
    link_type: draft.link_type || '',
  });
  renderThinkingPanel();
}

function thinkingMindDeleteLink(linkId) {
  linkId = String(linkId || _thinkingSelectedLinkId || '');
  var mapId = String(_thinkingMindSelectedId || '');
  if (!linkId || !mapId) return;
  _thinkingConfirm('Delete this link?', {
    label: 'Delete link', variant: 'btn-danger', title: 'Delete Mind Map link'
  }).then(function(ok) {
    if (!ok) return;
    _thinkingSend({ cmd: 'mind_map_link_delete', group: _thinkingGroup(), mind_map_id: mapId, link_id: linkId });
    renderThinkingPanel();
  });
}

function _renderThinkingTabs(noteTotal, mapTotal) {
  var active = String(_thinkingActiveTab || 'scratchpad');
  var html = '<div class="thinking-tabs" role="tablist" aria-label="Thinking surfaces">';
  html += '<button type="button" role="tab" class="thinking-tab' + (active === 'scratchpad' ? ' active' : '') + '" aria-selected="' + (active === 'scratchpad' ? 'true' : 'false') + '" onclick="thinkingSetTab(\'scratchpad\')">Scratchpad <span>' + _thinkingEsc(noteTotal) + '</span></button>';
  html += '<button type="button" role="tab" class="thinking-tab' + (active === 'mind-map' ? ' active' : '') + '" aria-selected="' + (active === 'mind-map' ? 'true' : 'false') + '" onclick="thinkingSetTab(\'mind-map\')">Mind Map <span>' + _thinkingEsc(mapTotal) + '</span></button>';
  html += '</div>';
  return html;
}

function _renderThinkingListEmpty(kind) {
  if (kind === 'map') return '<div class="thinking-empty">No active Mind Maps yet. Create one to sketch durable relationships.</div>';
  return '<div class="thinking-empty">No active rough notes yet. Create a scratchpad note for loose thinking.</div>';
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
  html += '<div class="thinking-list-toolbar"><div><strong>Scratchpad</strong><span>Rough, group-scoped notes</span></div><button type="button" class="btn-primary" onclick="thinkingScratchNew()">New note</button></div>';
  if (_thinkingScratchLoadingGroup === group && _thinkingScratchLoadedGroup !== group) {
    html += '<div class="thinking-loading">Loading scratchpad notes…</div>';
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
    return '<section class="thinking-editor thinking-editor-empty"><div class="thinking-empty-detail">Select or create a scratchpad note. Notes stay informal and do not become tasks, decisions, journals, or Planning records.</div></section>';
  }
  var note = noteId === THINKING_NEW_NOTE_ID ? null : state.thinking.scratchpad_notes[noteId];
  if (noteId !== THINKING_NEW_NOTE_ID && (!note || !_thinkingIsActive(note))) {
    return '<section class="thinking-editor thinking-editor-empty"><div class="thinking-empty-detail">This scratchpad note is no longer active.</div></section>';
  }
  var draft = _thinkingScratchDraft(noteId);
  var dirty = _thinkingScratchIsDirty(noteId, draft);
  var title = noteId === THINKING_NEW_NOTE_ID ? 'New scratchpad note' : (note.title || noteId);
  var status = noteId === THINKING_NEW_NOTE_ID ? 'Unsaved rough note' : (dirty ? 'Unsaved rough note edits' : 'Saved rough note');
  var html = '<section class="thinking-editor thinking-scratch-editor" id="thinking-scratch-editor">';
  html += '<div class="thinking-detail-head"><div><div class="thinking-kicker">Scratchpad note</div><h2>' + _thinkingEsc(title || 'Scratchpad note') + '</h2><div class="thinking-detail-id">' + _thinkingEsc(noteId === THINKING_NEW_NOTE_ID ? 'Draft' : noteId) + '</div></div>';
  if (noteId !== THINKING_NEW_NOTE_ID) html += '<button type="button" class="thinking-close" onclick="thinkingScratchSelect(\'\')" title="Close">×</button>';
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

function _renderMapList(group) {
  var maps = _thinkingMapsForGroup(group);
  if (!_thinkingMindSelectedId && maps.length) _thinkingMindSelectedId = String(maps[0].id || '');
  if (_thinkingMindSelectedId && _thinkingMindSelectedId !== THINKING_NEW_MAP_ID) {
    var selected = state.thinking.mind_maps[_thinkingMindSelectedId];
    if (!selected || !_thinkingIsActive(selected) || _thinkingItemGroup(selected) !== group) {
      _thinkingMindSelectedId = maps.length ? String(maps[0].id || '') : '';
      _thinkingSelectedNodeId = '';
      _thinkingSelectedLinkId = '';
    }
  }
  var html = '<aside class="thinking-list-pane thinking-map-list-pane">';
  html += '<div class="thinking-list-toolbar"><div><strong>Mind Maps</strong><span>Durable node/link sketches</span></div><button type="button" class="btn-primary" onclick="thinkingMindNew()">New map</button></div>';
  if (_thinkingMindLoadingGroup === group && _thinkingMindLoadedGroup !== group) {
    html += '<div class="thinking-loading">Loading Mind Maps…</div>';
  }
  html += '<div class="thinking-list" id="thinking-map-list-scroll">';
  if (_thinkingMindSelectedId === THINKING_NEW_MAP_ID) {
    html += '<button type="button" class="thinking-list-card selected draft" onclick="thinkingMindSelect(\'' + _thinkingJs(THINKING_NEW_MAP_ID) + '\')"><span class="thinking-list-title">New Mind Map</span><span class="thinking-list-meta">Draft · not saved yet</span></button>';
  }
  if (!maps.length) html += _renderThinkingListEmpty('map');
  maps.forEach(function(mindMap) {
    var mapId = String(mindMap.id || '');
    var selected = _thinkingMindSelectedId === mapId;
    html += '<button type="button" class="thinking-list-card' + (selected ? ' selected' : '') + '" onclick="thinkingMindSelect(\'' + _thinkingJs(mapId) + '\')">';
    html += '<span class="thinking-list-title">' + _thinkingEsc(mindMap.title || 'Untitled Mind Map') + '</span>';
    html += '<span class="thinking-list-body">' + _thinkingEsc(mindMap.description || 'Map relationships between ideas.') + '</span>';
    var counts = (Number(mindMap.node_count || 0) || 0) + ' nodes · ' + (Number(mindMap.link_count || 0) || 0) + ' links';
    html += '<span class="thinking-list-meta">' + _thinkingEsc(mapId + ' · ' + counts) + '</span>';
    html += '</button>';
  });
  html += '</div></aside>';
  return html;
}

function _renderMapEditorHeader(mapId, detail) {
  var map = (state.thinking.mind_maps && state.thinking.mind_maps[mapId]) || detail || {};
  var draft = _thinkingMapDraft(mapId);
  var dirty = _thinkingMapIsDirty(mapId, draft);
  var title = mapId === THINKING_NEW_MAP_ID ? 'New Mind Map' : (map.title || mapId);
  var html = '<div class="thinking-detail-head thinking-map-head"><div><div class="thinking-kicker">Mind Map</div><h2>' + _thinkingEsc(title || 'Mind Map') + '</h2><div class="thinking-detail-id">' + _thinkingEsc(mapId === THINKING_NEW_MAP_ID ? 'Draft' : mapId) + '</div></div>';
  if (mapId !== THINKING_NEW_MAP_ID) html += '<button type="button" class="thinking-close" onclick="thinkingMindSelect(\'\')" title="Close">×</button>';
  html += '</div>';
  html += '<div class="thinking-form thinking-map-title-form">';
  html += '<label for="thinking-map-title">Title</label><input id="thinking-map-title" data-thinking-field="map-title:' + _thinkingAttr(mapId) + '" value="' + _thinkingAttr(draft.title || '') + '" oninput="thinkingMindChanged()" autocomplete="off">';
  html += '<label for="thinking-map-description">Description</label><textarea id="thinking-map-description" data-thinking-field="map-description:' + _thinkingAttr(mapId) + '" rows="2" oninput="thinkingMindChanged()" placeholder="What is this sketch trying to clarify?">' + _thinkingEsc(draft.description || '') + '</textarea>';
  html += '</div>';
  html += '<div class="thinking-detail-actions thinking-map-actions"><span class="thinking-save-status" id="thinking-map-draft-status">' + _thinkingEsc(mapId === THINKING_NEW_MAP_ID ? 'Unsaved map' : (dirty ? 'Unsaved map edits' : 'Saved map shell')) + '</span>';
  if (mapId !== THINKING_NEW_MAP_ID) {
    html += '<button type="button" class="btn-secondary" onclick="thinkingMindArchive(\'' + _thinkingJs(mapId) + '\')">Archive</button>';
    html += '<button type="button" class="btn-secondary danger" onclick="thinkingMindDelete(\'' + _thinkingJs(mapId) + '\')">Delete</button>';
  }
  html += '<button type="button" class="btn-primary" onclick="thinkingMindSaveMap()"' + (_thinkingSaving ? ' disabled' : '') + '>' + (_thinkingSaving ? 'Saving…' : (mapId === THINKING_NEW_MAP_ID ? 'Create map' : 'Save map')) + '</button>';
  html += '</div>';
  return html;
}

function _renderMapCanvas(detail) {
  var nodes = _thinkingActiveNodes(detail);
  var links = _thinkingActiveLinks(detail);
  var positions = _thinkingPositionMap(detail);
  var html = '<div class="thinking-map-canvas-wrap" id="thinking-map-canvas-wrap" aria-label="Mind Map canvas">';
  html += '<svg class="thinking-map-links" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">';
  links.forEach(function(link) {
    var sourceId = String(link.source_node_id || '');
    var targetId = String(link.target_node_id || '');
    var a = positions[sourceId];
    var b = positions[targetId];
    if (!a || !b) return;
    html += '<line class="thinking-map-link-line" data-link-id="' + _thinkingAttr(link.id || '') + '" data-source-node-id="' + _thinkingAttr(sourceId) + '" data-target-node-id="' + _thinkingAttr(targetId) + '" x1="' + _thinkingAttr(a.x) + '" y1="' + _thinkingAttr(a.y) + '" x2="' + _thinkingAttr(b.x) + '" y2="' + _thinkingAttr(b.y) + '"></line>';
  });
  html += '</svg>';
  if (!nodes.length) {
    html += '<div class="thinking-map-empty-canvas">Add nodes to start a durable sketch.</div>';
  }
  nodes.forEach(function(node, index) {
    var nodeId = String(node.id || '');
    var pos = positions[nodeId] || _thinkingDefaultNodePosition(index, nodes.length);
    var color = String(node.color || '').trim();
    var selected = _thinkingSelectedNodeId === nodeId;
    html += '<button type="button" class="thinking-map-node' + (selected ? ' selected' : '') + '" data-node-id="' + _thinkingAttr(nodeId) + '" style="left:' + _thinkingAttr(pos.x) + '%;top:' + _thinkingAttr(pos.y) + '%;' + (color ? '--thinking-node-color:' + _thinkingAttr(color) + ';' : '') + '" onclick="thinkingMindSelectNode(\'' + _thinkingJs(nodeId) + '\')" onpointerdown="thinkingMindNodePointerDown(event,\'' + _thinkingJs(nodeId) + '\')" onkeydown="thinkingMindNodeKeydown(event,\'' + _thinkingJs(nodeId) + '\')">';
    html += '<span class="thinking-map-node-label">' + _thinkingEsc(node.label || node.title || 'Untitled node') + '</span>';
    if (node.node_type) html += '<span class="thinking-map-node-type">' + _thinkingEsc(node.node_type) + '</span>';
    if (node.notes) html += '<span class="thinking-map-node-notes">' + _thinkingEsc(String(node.notes).split(/\n/)[0]) + '</span>';
    html += '</button>';
  });
  html += '</div>';
  return html;
}

function _renderNodeOptions(nodes, selectedId) {
  var html = '';
  nodes.forEach(function(node) {
    var nodeId = String(node.id || '');
    html += '<option value="' + _thinkingAttr(nodeId) + '"' + (String(selectedId || '') === nodeId ? ' selected' : '') + '>' + _thinkingEsc(node.label || node.title || nodeId) + '</option>';
  });
  return html;
}

function _renderNodeTools(detail) {
  var mapId = String(_thinkingMindSelectedId || '');
  var draft = _thinkingNodeDraftsByMap[mapId] || {};
  var selectedNode = _thinkingSelectedNodeId ? _thinkingNodeById(detail, _thinkingSelectedNodeId) : null;
  if (selectedNode && selectedNode.deleted) selectedNode = null;
  var editDraft = selectedNode ? (_thinkingNodeEditDraftsById[selectedNode.id] || {
    label: selectedNode.label || selectedNode.title || '',
    notes: selectedNode.notes || '',
    node_type: selectedNode.node_type || '',
    color: selectedNode.color || '',
  }) : null;
  var html = '<section class="thinking-map-tool-card"><h3>Add node</h3>';
  html += '<div class="thinking-form thinking-inline-form">';
  html += '<label for="thinking-node-new-label">Label</label><input id="thinking-node-new-label" data-thinking-field="node-new-label:' + _thinkingAttr(mapId) + '" value="' + _thinkingAttr(draft.label || '') + '" oninput="thinkingMindNodeChanged()" autocomplete="off" placeholder="Idea, question, constraint…">';
  html += '<label for="thinking-node-new-type">Type</label><input id="thinking-node-new-type" data-thinking-field="node-new-type:' + _thinkingAttr(mapId) + '" value="' + _thinkingAttr(draft.node_type || '') + '" oninput="thinkingMindNodeChanged()" autocomplete="off" placeholder="idea / risk / evidence">';
  html += '<label for="thinking-node-new-color">Color</label><input id="thinking-node-new-color" data-thinking-field="node-new-color:' + _thinkingAttr(mapId) + '" value="' + _thinkingAttr(draft.color || '') + '" oninput="thinkingMindNodeChanged()" autocomplete="off" placeholder="#58a6ff">';
  html += '<label for="thinking-node-new-notes">Notes</label><textarea id="thinking-node-new-notes" data-thinking-field="node-new-notes:' + _thinkingAttr(mapId) + '" rows="3" oninput="thinkingMindNodeChanged()" placeholder="Optional node notes">' + _thinkingEsc(draft.notes || '') + '</textarea>';
  html += '</div><div class="thinking-tool-actions"><button type="button" class="btn-primary" onclick="thinkingMindAddNode()">Add node</button></div></section>';
  html += '<section class="thinking-map-tool-card"><h3>Selected node</h3>';
  if (!selectedNode) {
    html += '<div class="thinking-empty compact">Select a node to edit. Drag nodes, or focus a node and use arrow keys to move it.</div>';
  } else {
    html += '<div class="thinking-form thinking-inline-form">';
    html += '<label for="thinking-node-edit-label">Label</label><input id="thinking-node-edit-label" data-thinking-field="node-edit-label:' + _thinkingAttr(selectedNode.id) + '" value="' + _thinkingAttr(editDraft.label || '') + '" oninput="thinkingMindNodeChanged()" autocomplete="off">';
    html += '<label for="thinking-node-edit-type">Type</label><input id="thinking-node-edit-type" data-thinking-field="node-edit-type:' + _thinkingAttr(selectedNode.id) + '" value="' + _thinkingAttr(editDraft.node_type || '') + '" oninput="thinkingMindNodeChanged()" autocomplete="off">';
    html += '<label for="thinking-node-edit-color">Color</label><input id="thinking-node-edit-color" data-thinking-field="node-edit-color:' + _thinkingAttr(selectedNode.id) + '" value="' + _thinkingAttr(editDraft.color || '') + '" oninput="thinkingMindNodeChanged()" autocomplete="off">';
    html += '<label for="thinking-node-edit-notes">Notes</label><textarea id="thinking-node-edit-notes" data-thinking-field="node-edit-notes:' + _thinkingAttr(selectedNode.id) + '" rows="4" oninput="thinkingMindNodeChanged()">' + _thinkingEsc(editDraft.notes || '') + '</textarea>';
    html += '</div><div class="thinking-tool-actions"><button type="button" class="btn-secondary" onclick="thinkingMindMoveNode(\'' + _thinkingJs(selectedNode.id) + '\',-5,0)">←</button><button type="button" class="btn-secondary" onclick="thinkingMindMoveNode(\'' + _thinkingJs(selectedNode.id) + '\',0,-5)">↑</button><button type="button" class="btn-secondary" onclick="thinkingMindMoveNode(\'' + _thinkingJs(selectedNode.id) + '\',0,5)">↓</button><button type="button" class="btn-secondary" onclick="thinkingMindMoveNode(\'' + _thinkingJs(selectedNode.id) + '\',5,0)">→</button><button type="button" class="btn-secondary danger" onclick="thinkingMindDeleteNode(\'' + _thinkingJs(selectedNode.id) + '\')">Delete</button><button type="button" class="btn-primary" onclick="thinkingMindSaveNode()">Save node</button></div>';
  }
  html += '</section>';
  return html;
}

function _renderLinkTools(detail) {
  var mapId = String(_thinkingMindSelectedId || '');
  var nodes = _thinkingActiveNodes(detail);
  var links = _thinkingActiveLinks(detail);
  var draft = _thinkingLinkDraftsByMap[mapId] || {};
  if (!draft.source_node_id && nodes[0]) draft.source_node_id = nodes[0].id;
  if (!draft.target_node_id && nodes[1]) draft.target_node_id = nodes[1].id;
  var selectedLink = _thinkingSelectedLinkId ? _thinkingLinkById(detail, _thinkingSelectedLinkId) : null;
  if (selectedLink && selectedLink.deleted) selectedLink = null;
  var editDraft = selectedLink ? (_thinkingLinkEditDraftsById[selectedLink.id] || {
    source_node_id: selectedLink.source_node_id || '',
    target_node_id: selectedLink.target_node_id || '',
    label: selectedLink.label || '',
    link_type: selectedLink.link_type || '',
  }) : null;
  var html = '<section class="thinking-map-tool-card"><h3>Links</h3>';
  html += '<div class="thinking-link-list">';
  if (!links.length) html += '<div class="thinking-empty compact">No active links.</div>';
  links.forEach(function(link) {
    var linkId = String(link.id || '');
    var source = _thinkingNodeById(detail, link.source_node_id) || {};
    var target = _thinkingNodeById(detail, link.target_node_id) || {};
    html += '<button type="button" class="thinking-link-row' + (_thinkingSelectedLinkId === linkId ? ' selected' : '') + '" onclick="thinkingMindSelectLink(\'' + _thinkingJs(linkId) + '\')">';
    html += '<span class="thinking-link-label">' + _thinkingEsc(link.label || link.link_type || 'Link') + '</span>';
    html += '<span class="thinking-link-nodes">' + _thinkingEsc((source.label || source.title || link.source_node_id || '?') + ' → ' + (target.label || target.title || link.target_node_id || '?')) + '</span>';
    html += '</button>';
  });
  html += '</div>';
  html += '<div class="thinking-form thinking-inline-form thinking-link-form">';
  html += '<label for="thinking-link-new-source">Source</label><select id="thinking-link-new-source" data-thinking-field="link-new-source:' + _thinkingAttr(mapId) + '" onchange="thinkingMindLinkChanged()">' + _renderNodeOptions(nodes, draft.source_node_id) + '</select>';
  html += '<label for="thinking-link-new-target">Target</label><select id="thinking-link-new-target" data-thinking-field="link-new-target:' + _thinkingAttr(mapId) + '" onchange="thinkingMindLinkChanged()">' + _renderNodeOptions(nodes, draft.target_node_id) + '</select>';
  html += '<label for="thinking-link-new-label">Label</label><input id="thinking-link-new-label" data-thinking-field="link-new-label:' + _thinkingAttr(mapId) + '" value="' + _thinkingAttr(draft.label || '') + '" oninput="thinkingMindLinkChanged()" autocomplete="off" placeholder="supports / blocks / inspires">';
  html += '<label for="thinking-link-new-type">Type</label><input id="thinking-link-new-type" data-thinking-field="link-new-type:' + _thinkingAttr(mapId) + '" value="' + _thinkingAttr(draft.link_type || '') + '" oninput="thinkingMindLinkChanged()" autocomplete="off" placeholder="optional type">';
  html += '</div><div class="thinking-tool-actions"><button type="button" class="btn-primary" onclick="thinkingMindAddLink()"' + (nodes.length < 2 ? ' disabled' : '') + '>Add link</button></div></section>';
  html += '<section class="thinking-map-tool-card"><h3>Selected link</h3>';
  if (!selectedLink) {
    html += '<div class="thinking-empty compact">Select a link to edit or delete.</div>';
  } else {
    html += '<div class="thinking-form thinking-inline-form thinking-link-form">';
    html += '<label for="thinking-link-edit-source">Source</label><select id="thinking-link-edit-source" data-thinking-field="link-edit-source:' + _thinkingAttr(selectedLink.id) + '" onchange="thinkingMindLinkChanged()">' + _renderNodeOptions(nodes, editDraft.source_node_id) + '</select>';
    html += '<label for="thinking-link-edit-target">Target</label><select id="thinking-link-edit-target" data-thinking-field="link-edit-target:' + _thinkingAttr(selectedLink.id) + '" onchange="thinkingMindLinkChanged()">' + _renderNodeOptions(nodes, editDraft.target_node_id) + '</select>';
    html += '<label for="thinking-link-edit-label">Label</label><input id="thinking-link-edit-label" data-thinking-field="link-edit-label:' + _thinkingAttr(selectedLink.id) + '" value="' + _thinkingAttr(editDraft.label || '') + '" oninput="thinkingMindLinkChanged()" autocomplete="off">';
    html += '<label for="thinking-link-edit-type">Type</label><input id="thinking-link-edit-type" data-thinking-field="link-edit-type:' + _thinkingAttr(selectedLink.id) + '" value="' + _thinkingAttr(editDraft.link_type || '') + '" oninput="thinkingMindLinkChanged()" autocomplete="off">';
    html += '</div><div class="thinking-tool-actions"><button type="button" class="btn-secondary danger" onclick="thinkingMindDeleteLink(\'' + _thinkingJs(selectedLink.id) + '\')">Delete link</button><button type="button" class="btn-primary" onclick="thinkingMindSaveLink()">Save link</button></div>';
  }
  html += '</section>';
  return html;
}

function _renderMapDetail() {
  var mapId = String(_thinkingMindSelectedId || '');
  if (!mapId) {
    return '<section class="thinking-map-detail thinking-editor-empty"><div class="thinking-empty-detail">Select or create a Mind Map. Maps are durable group-scoped sketches, not a full whiteboard.</div></section>';
  }
  if (mapId === THINKING_NEW_MAP_ID) {
    return '<section class="thinking-map-detail" id="thinking-map-detail">' + _renderMapEditorHeader(mapId, null) + '<div class="thinking-empty-detail padded">Create the map shell first, then add nodes and links.</div></section>';
  }
  thinkingMindLoadDetail(mapId);
  var detail = _thinkingMapDetail(mapId);
  var map = (state.thinking.mind_maps && state.thinking.mind_maps[mapId]) || detail;
  if (!map || !_thinkingIsActive(map)) {
    return '<section class="thinking-map-detail thinking-editor-empty"><div class="thinking-empty-detail">This Mind Map is no longer active.</div></section>';
  }
  var html = '<section class="thinking-map-detail" id="thinking-map-detail">';
  html += _renderMapEditorHeader(mapId, detail || map);
  if (_thinkingMindDetailLoadingId === mapId && !detail) {
    html += '<div class="thinking-loading">Loading map nodes and links…</div>';
    html += '</section>';
    return html;
  }
  detail = detail || Object.assign({}, map, { nodes: [], links: [] });
  var nodes = _thinkingActiveNodes(detail);
  var links = _thinkingActiveLinks(detail);
  html += '<div class="thinking-map-summary-row"><span>' + _thinkingEsc(nodes.length) + ' nodes</span><span>' + _thinkingEsc(links.length) + ' links</span><span>Drag or use arrow keys to persist positions.</span></div>';
  html += _renderMapCanvas(detail);
  html += '<div class="thinking-map-tools">' + _renderNodeTools(detail) + _renderLinkTools(detail) + '</div>';
  html += '<div class="thinking-handoff-affordance"><strong>Idea Brief handoff affordance</strong><span>Map title, description, node notes, coordinates, and links are preserved as stable references for Creative Architect Idea Brief workflows; creating or proposing a brief remains proposal-only and never dispatches implementation work.</span></div>';
  html += '</section>';
  return html;
}

function _renderThinkingMindMap(group) {
  thinkingEnsureMindMapsLoaded();
  return '<div class="thinking-workspace thinking-map-workspace" id="thinking-workspace">'
    + _renderMapList(group)
    + _renderMapDetail()
    + '</div>';
}

function renderThinkingPanel() {
  var panel = document.getElementById('panel-thinking');
  if (!panel) return;
  _thinkingEnsureState();
  _thinkingCaptureDrafts();
  var snapshot = null;
  if (typeof _captureSurfaceState === 'function') {
    snapshot = _captureSurfaceState(panel, {
      scrollSelectors: [
        ':root',
        '#thinking-workspace',
        '#thinking-scratch-list',
        '#thinking-scratch-editor',
        '#thinking-map-list-scroll',
        '#thinking-map-detail',
        '#thinking-map-canvas-wrap',
      ],
      captureFocusKey: function(active) {
        if (active && active.dataset && active.dataset.thinkingField) {
          return '[data-thinking-field="' + active.dataset.thinkingField + '"]';
        }
        return '';
      },
    });
  }
  var group = _thinkingGroup();
  var notes = _thinkingNotesForGroup(group);
  var maps = _thinkingMapsForGroup(group);
  var activeTab = String(_thinkingActiveTab || 'scratchpad');
  var html = '<div class="thinking-panel">';
  html += '<div class="tpled-header thinking-header"><div class="tpled-header-copy"><div class="tpled-header-title-row">' + _thinkingConnectedNodesIcon('thinking-header-icon') + '<span class="tpled-header-title">Thinking</span></div>';
  html += '<div class="tpled-header-subtitle">Scratchpad and Mind Map are group-scoped thinking tools for ' + _thinkingEsc(group || 'all groups') + '; they stay separate from Planning.</div></div>';
  html += '<div class="tpled-header-controls"><span class="thinking-total">' + _thinkingEsc(notes.length + ' notes · ' + maps.length + ' maps') + '</span><button class="tpled-new-btn" onclick="thinkingRefresh()" title="Refresh Thinking data">&#x21BB;</button></div></div>';
  html += _renderThinkingTabs(notes.length, maps.length);
  if (_thinkingLastError) html += '<div class="thinking-error">' + _thinkingEsc(_thinkingLastError) + '</div>';
  if (activeTab === 'mind-map') html += _renderThinkingMindMap(group);
  else html += _renderThinkingScratchpad(group);
  html += '</div>';
  panel.innerHTML = html;
  if (typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(panel, snapshot, {
      resolveFocus: function(root, focus) {
        if (focus && focus.key && root && root.querySelector) return root.querySelector(focus.key);
        return null;
      },
    });
  }
}

/* ------------------------------------------------------------------ */
/* Mission Control panel app — read-only operator readiness console    */
/* ------------------------------------------------------------------ */

var MISSION_CONTROL_SECTION_ORDER = [
  'needs_operator_now',
  'at_risk_watchlist',
  'in_flight',
  'recently_completed',
];
var MISSION_CONTROL_SECTION_META = {
  needs_operator_now: {
    title: 'Needs operator now',
    subtitle: 'Gates and recommended next actions that need a human decision or validation.',
    empty: 'No operator gates are waiting right now.',
  },
  at_risk_watchlist: {
    title: 'At-risk watchlist',
    subtitle: 'Risks worth inspecting before they become blocking.',
    empty: 'No active risks in this group.',
  },
  in_flight: {
    title: 'In flight',
    subtitle: 'Healthy active work, kept compact so gates stay first.',
    empty: 'No healthy in-flight work to show.',
  },
  recently_completed: {
    title: 'Recently completed',
    subtitle: 'Recent completions for operator confidence.',
    empty: 'No recent completions in the configured window.',
  },
};

var _missionControlLoadedGroup = null;
var _missionControlLoadingGroup = null;
var _missionControlData = null;
var _missionControlLastError = '';
var _missionControlFilter = '';
var _missionControlSelectedCardId = '';
var _missionControlCollapsedSections = {};
var _missionControlRequestSeq = 0;

function _missionControlGroup() {
  if (typeof _currentGroup === 'function') return _currentGroup() || '';
  return (state && state.active_group) || '';
}

function _missionControlPanelVisible() {
  return !!(
    (typeof _panelAppVisible === 'function' && _panelAppVisible('mission-control'))
    || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'mission-control')
  );
}

function missionControlEnsureLoaded(opts) {
  opts = opts || {};
  var group = _missionControlGroup();
  if (!group && !opts.force) return false;
  if (!opts.force && (_missionControlLoadedGroup === group || _missionControlLoadingGroup === group)) return false;
  _missionControlLoadingGroup = group;
  _missionControlLastError = '';
  _missionControlRequestSeq += 1;
  if (typeof send === 'function') {
    send({
      cmd: 'get_mission_control',
      group: group,
      limit_per_section: 20,
      include_recent_completed: true,
    });
  }
  return true;
}

function missionControlRefresh() {
  missionControlEnsureLoaded({ force: true });
  renderMissionControlPanel();
}

function missionControlBeginGroupSwitch() {
  var group = _missionControlGroup() || '';
  if (_missionControlLoadedGroup !== group) {
    _missionControlData = null;
    _missionControlSelectedCardId = '';
    _missionControlLastError = '';
  }
  missionControlEnsureLoaded({ force: true });
  renderMissionControlPanel();
}

function missionControlReceiveSummary(msg) {
  if (!msg || msg.type !== 'mission_control_summary') return false;
  var group = String(msg.group || '');
  var currentGroup = _missionControlGroup() || '';
  if (group !== currentGroup) {
    if (_missionControlLoadingGroup === group) _missionControlLoadingGroup = null;
    return false;
  }
  _missionControlLoadingGroup = null;
  _missionControlLoadedGroup = group;
  _missionControlData = msg;
  _missionControlLastError = '';
  if (_missionControlSelectedCardId && !_missionControlFindCard(_missionControlSelectedCardId)) {
    _missionControlSelectedCardId = '';
  }
  renderMissionControlPanel();
  return true;
}

function missionControlHandleError(msg) {
  if (!_missionControlPanelVisible() && !_missionControlLoadingGroup) return false;
  var text = String((msg && msg.message) || 'Mission Control command failed');
  if (text.toLowerCase().indexOf('mission') < 0
      && text.toLowerCase().indexOf('group required') < 0
      && !_missionControlLoadingGroup) {
    return false;
  }
  _missionControlLastError = text;
  _missionControlLoadingGroup = null;
  renderMissionControlPanel();
  return true;
}

function missionControlSetFilter(value) {
  _missionControlFilter = String(value || '');
  renderMissionControlPanel();
}

function missionControlToggleSection(sectionKey) {
  sectionKey = String(sectionKey || '');
  if (!sectionKey) return;
  _missionControlCollapsedSections[sectionKey] = !_missionControlCollapsedSections[sectionKey];
  renderMissionControlPanel();
}

function missionControlSelectCard(cardId) {
  cardId = String(cardId || '').trim();
  _missionControlSelectedCardId = (_missionControlSelectedCardId === cardId) ? '' : cardId;
  renderMissionControlPanel();
}

function _missionControlSummaryForCurrentGroup() {
  var group = _missionControlGroup() || '';
  if (!_missionControlData || String(_missionControlData.group || '') !== group) return null;
  return _missionControlData;
}

function _missionControlSection(summary, sectionKey) {
  var sections = summary && summary.sections;
  var section = sections && sections[sectionKey];
  if (!section || typeof section !== 'object') return { count: 0, items: [], truncated: false };
  return {
    count: Number(section.count || 0) || 0,
    items: Array.isArray(section.items) ? section.items : [],
    truncated: !!section.truncated,
  };
}

function _missionControlAllCards(summary) {
  var cards = [];
  MISSION_CONTROL_SECTION_ORDER.forEach(function(key) {
    _missionControlSection(summary, key).items.forEach(function(card) { cards.push(card); });
  });
  return cards;
}

function _missionControlFindCard(cardId) {
  cardId = String(cardId || '');
  if (!cardId || !_missionControlData) return null;
  var cards = _missionControlAllCards(_missionControlData);
  for (var i = 0; i < cards.length; i++) {
    if (String(cards[i].id || '') === cardId) return cards[i];
  }
  return null;
}

function _missionControlOwnerLabel(owner) {
  owner = owner || {};
  return String(
    owner.agent_name || owner.agent_slug || owner.agent_id
    || owner.assigned_engineer_id || owner.assigned_architect_id
    || owner.created_by_engineer_id || owner.created_by_architect_id || ''
  ).trim();
}

function _missionControlCardSearchText(card) {
  card = card || {};
  var parts = [
    card.id,
    card.title,
    card.kind,
    card.gate,
    card.reason,
    card.recommended_next_action,
    card.primary_task_id,
    _missionControlOwnerLabel(card.owner),
  ];
  var ref = card.ref || {};
  parts.push(ref.kind, ref.id);
  (card.task_ids || []).forEach(function(value) { parts.push(value); });
  (card.evidence_chips || []).forEach(function(value) { parts.push(value); });
  (card.caveat_chips || []).forEach(function(value) { parts.push(value); });
  (card.deep_links || []).forEach(function(link) {
    if (!link || typeof link !== 'object') return;
    Object.keys(link).forEach(function(key) { parts.push(link[key]); });
  });
  return parts.join(' ').toLowerCase();
}

function _missionControlFilterCards(cards) {
  var q = String(_missionControlFilter || '').trim().toLowerCase();
  if (!q) return cards.slice();
  return (cards || []).filter(function(card) {
    return _missionControlCardSearchText(card).indexOf(q) >= 0;
  });
}

function _missionControlActionLabel(action) {
  var text = String(action || '').trim();
  if (!text) return 'Inspect source surface';
  return text.replace(/_/g, ' ');
}

function _missionControlSeverityClass(severity) {
  var value = String(severity || 'medium').toLowerCase();
  if (['critical', 'high', 'medium', 'low', 'none'].indexOf(value) < 0) value = 'medium';
  return value;
}

function _missionControlRefLabel(card) {
  card = card || {};
  var ref = card.ref || {};
  var refId = String(ref.id || card.primary_task_id || card.stream_id || card.id || '').trim();
  var kind = String(ref.kind || card.kind || 'ref').trim();
  if (refId) return kind + ':' + refId;
  return kind;
}

function _missionControlTimestampLabel(card) {
  var ts = card && card.timestamps && typeof card.timestamps === 'object' ? card.timestamps : {};
  var labels = [];
  var preferred = ['set_at', 'updated_at', 'last_activity_at', 'lane_entered_at', 'generated_at', 'created_at', 'boot_timestamp'];
  preferred.forEach(function(key) {
    var value = ts[key];
    if (value === undefined || value === null || value === '') return;
    if (labels.length >= 3) return;
    labels.push(key.replace(/_/g, ' ') + ': ' + String(value));
  });
  return labels;
}

function _missionControlChipHtml(value, cls) {
  value = String(value || '').trim();
  if (!value) return '';
  return '<span class="mc-chip ' + esc(cls || '') + '">' + esc(value) + '</span>';
}

function _missionControlDeepLinkLabel(link) {
  link = link || {};
  var surface = String(link.surface || 'surface').trim();
  var kind = String(link.kind || 'inspect').trim();
  var id = String(link.task_id || link.agent_id || link.stream_id || link.group || link.id || '').trim();
  return surface + ' / ' + kind + (id ? ' / ' + id : '');
}

function _missionControlDeepLinksHtml(card) {
  var links = Array.isArray(card && card.deep_links) ? card.deep_links : [];
  if (!links.length) return '<span class="mc-deeplink-empty">No deep-link descriptor</span>';
  var html = '';
  links.forEach(function(link) {
    html += '<span class="mc-deeplink" title="Read-only descriptor; open the named surface to act.">'
      + esc(_missionControlDeepLinkLabel(link)) + '</span>';
  });
  return html;
}

function _missionControlCardHtml(card, sectionKey) {
  card = card || {};
  var cardId = String(card.id || '');
  var selected = cardId && cardId === _missionControlSelectedCardId;
  var severity = _missionControlSeverityClass(card.severity);
  var owner = _missionControlOwnerLabel(card.owner) || 'unassigned';
  var evidence = Array.isArray(card.evidence_chips) ? card.evidence_chips : [];
  var caveats = Array.isArray(card.caveat_chips) ? card.caveat_chips : [];
  var times = _missionControlTimestampLabel(card);
  var html = '';
  html += '<article class="mc-card mc-severity-' + esc(severity) + (selected ? ' selected' : '') + '"'
    + ' data-card-id="' + esc(cardId) + '" data-section="' + esc(sectionKey || '') + '"'
    + ' onclick="missionControlSelectCard(\'' + esc(cardId) + '\')">';
  html += '<div class="mc-card-topline">';
  html += '<span class="mc-ref">' + esc(_missionControlRefLabel(card)) + '</span>';
  html += '<span class="mc-card-id">' + esc(cardId || 'no-id') + '</span>';
  html += '<span class="mc-card-kind">' + esc(card.kind || 'card') + '</span>';
  html += '</div>';
  html += '<div class="mc-card-title">' + esc(card.title || cardId || 'Untitled') + '</div>';
  html += '<div class="mc-card-meta">';
  html += '<span>owner: ' + esc(owner) + '</span>';
  html += '<span>gate: ' + esc(card.gate || 'state') + '</span>';
  html += '</div>';
  if (card.reason) {
    html += '<div class="mc-card-reason"><span>Why</span>' + esc(card.reason) + '</div>';
  }
  html += '<div class="mc-card-action"><span>Next</span>' + esc(_missionControlActionLabel(card.recommended_next_action)) + '</div>';
  if (evidence.length || caveats.length) {
    html += '<div class="mc-card-chips">';
    evidence.forEach(function(chip) { html += _missionControlChipHtml(chip, 'mc-chip-evidence'); });
    caveats.forEach(function(chip) { html += _missionControlChipHtml(chip, 'mc-chip-caveat'); });
    html += '</div>';
  }
  if (times.length) {
    html += '<div class="mc-card-times">';
    times.forEach(function(label) { html += '<span>' + esc(label) + '</span>'; });
    html += '</div>';
  }
  html += '<div class="mc-card-links">' + _missionControlDeepLinksHtml(card) + '</div>';
  html += '</article>';
  return html;
}

function _missionControlSectionHtml(summary, sectionKey) {
  var meta = MISSION_CONTROL_SECTION_META[sectionKey] || { title: sectionKey, subtitle: '', empty: 'No cards.' };
  var section = _missionControlSection(summary, sectionKey);
  var filtered = _missionControlFilterCards(section.items);
  var collapsed = !!_missionControlCollapsedSections[sectionKey];
  var html = '';
  html += '<section class="mc-section" data-section="' + esc(sectionKey) + '">';
  html += '<div class="mc-section-head">';
  html += '<button type="button" class="mc-section-toggle" onclick="missionControlToggleSection(\'' + esc(sectionKey) + '\')" aria-expanded="' + (collapsed ? 'false' : 'true') + '">'
    + '<span class="mc-section-caret">' + (collapsed ? '&#9656;' : '&#9662;') + '</span>'
    + '<span class="mc-section-title">' + esc(meta.title) + '</span>'
    + '<span class="mc-section-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">' + esc(String(section.count)) + '</span>'
    + '</button>';
  if (section.truncated) html += '<span class="mc-section-truncated">truncated</span>';
  html += '</div>';
  html += '<div class="mc-section-subtitle">' + esc(meta.subtitle) + '</div>';
  if (!collapsed) {
    html += '<div class="mc-card-list">';
    if (!filtered.length) {
      html += '<div class="mc-empty-inline">' + esc(_missionControlFilter ? 'No cards match the current filter.' : meta.empty) + '</div>';
    } else {
      filtered.forEach(function(card) { html += _missionControlCardHtml(card, sectionKey); });
    }
    html += '</div>';
  }
  html += '</section>';
  return html;
}

function _missionControlSourceFreshnessHtml(summary) {
  var source = summary && summary.source_freshness && typeof summary.source_freshness === 'object'
    ? summary.source_freshness
    : {};
  var keys = Object.keys(source).sort();
  if (!keys.length) return '';
  var html = '<div class="mc-source-freshness"><div class="mc-rail-title">Source freshness</div>';
  keys.forEach(function(key) {
    var item = source[key] || {};
    var stateName = String(item.state || 'unknown');
    var cls = stateName === 'error' ? 'error' : (stateName === 'ok' ? 'ok' : 'muted');
    var detail = item.error ? String(item.error) : (item.count !== undefined ? 'count ' + item.count : stateName);
    html += '<div class="mc-source-row mc-source-' + esc(cls) + '">'
      + '<span>' + esc(key) + '</span><strong>' + esc(stateName) + '</strong>'
      + '<em>' + esc(detail) + '</em></div>';
  });
  html += '</div>';
  return html;
}

function _missionControlSelectedDetailHtml(summary) {
  var card = _missionControlSelectedCardId ? _missionControlFindCard(_missionControlSelectedCardId) : null;
  if (!card) {
    var nowCount = _missionControlSection(summary, 'needs_operator_now').count;
    return '<aside class="mc-detail" id="mission-control-detail">'
      + '<div class="mc-rail-title">Read-only detail</div>'
      + '<p>' + esc(nowCount > 0
        ? 'Operator gates are listed first. Select a card to see its descriptors.'
        : 'Select any card to inspect its source descriptors and timestamps.') + '</p>'
      + _missionControlSourceFreshnessHtml(summary)
      + '</aside>';
  }
  var html = '<aside class="mc-detail" id="mission-control-detail">';
  html += '<div class="mc-rail-title">Selected card</div>';
  html += '<div class="mc-detail-title">' + esc(card.title || card.id || 'Selected card') + '</div>';
  html += '<div class="mc-detail-row"><span>ID</span><code>' + esc(card.id || '') + '</code></div>';
  html += '<div class="mc-detail-row"><span>Ref</span><code>' + esc(_missionControlRefLabel(card)) + '</code></div>';
  html += '<div class="mc-detail-row"><span>Recommended</span><strong>' + esc(_missionControlActionLabel(card.recommended_next_action)) + '</strong></div>';
  html += '<div class="mc-detail-block"><span>Why it matters</span><p>' + esc(card.reason || 'No reason supplied.') + '</p></div>';
  html += '<div class="mc-detail-block"><span>Deep-link descriptors</span><div class="mc-detail-links">' + _missionControlDeepLinksHtml(card) + '</div></div>';
  html += '<div class="mc-detail-block"><span>Caveats</span><div class="mc-card-chips">';
  (card.caveat_chips || []).forEach(function(chip) { html += _missionControlChipHtml(chip, 'mc-chip-caveat'); });
  if (!(card.caveat_chips || []).length) html += '<span class="mc-muted">None</span>';
  html += '</div></div>';
  html += _missionControlSourceFreshnessHtml(summary);
  html += '</aside>';
  return html;
}

function _missionControlShellHtml(summary) {
  var group = _missionControlGroup() || '';
  var counts = summary && summary.counts ? summary.counts : {};
  var operatorNow = Number(counts.needs_operator_now || _missionControlSection(summary, 'needs_operator_now').count || 0) || 0;
  var hasOperatorNow = operatorNow > 0;
  var total = Number(counts.total_cards || 0) || 0;
  var html = '';
  html += '<div class="mission-control-panel' + (hasOperatorNow ? ' has-operator-now' : '') + '">';
  html += '<div class="tpled-header mc-header">';
  html += '<div class="tpled-header-copy"><div class="tpled-header-title-row"><span class="tpled-header-title">Mission Control</span>'
    + (hasOperatorNow ? '<span class="mc-now-badge">operator gates: ' + esc(String(operatorNow)) + '</span>' : '')
    + '</div>';
  html += '<div class="tpled-header-subtitle">Read-only readiness console for ' + esc(group || 'all groups') + '. Actions stay on existing surfaces.</div></div>';
  html += '<div class="tpled-header-controls mc-controls">';
  html += '<input id="mission-control-filter" class="mc-filter" value="' + esc(_missionControlFilter) + '" oninput="missionControlSetFilter(this.value)" placeholder="Filter cards…" />';
  html += '<span class="mc-total ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">' + esc(String(total)) + ' cards</span>';
  html += '<button class="tpled-new-btn" onclick="missionControlRefresh()" title="Refresh read-only summary">&#x21BB;</button>';
  html += '</div></div>';
  if (_missionControlLastError) html += '<div class="mc-error">' + esc(_missionControlLastError) + '</div>';
  html += '<div class="mc-workspace" id="mission-control-workspace">';
  html += '<main class="mc-main" id="mission-control-main">';
  if (total <= 0) {
    html += '<div class="mc-empty-state">Mission Control is clear for this group. No operator gates, watchlist risks, in-flight cards, or recent completions were returned.</div>';
  }
  MISSION_CONTROL_SECTION_ORDER.forEach(function(key) { html += _missionControlSectionHtml(summary, key); });
  html += '</main>';
  html += _missionControlSelectedDetailHtml(summary);
  html += '</div></div>';
  return html;
}

function renderMissionControlPanel() {
  var panel = document.getElementById('panel-mission-control');
  if (!panel) return;
  var snapshot = null;
  if (typeof _captureSurfaceState === 'function') {
    snapshot = _captureSurfaceState(panel, {
      scrollSelectors: [
        '#mission-control-workspace',
        '#mission-control-main',
        '#mission-control-detail',
      ],
    });
  }
  var group = _missionControlGroup();
  if (!_missionControlLastError) missionControlEnsureLoaded();
  var summary = _missionControlSummaryForCurrentGroup();
  var html = '';
  if (!summary && _missionControlLoadingGroup === group) {
    html = '<div class="mission-control-panel"><div class="tpled-header mc-header">'
      + '<div class="tpled-header-copy"><div class="tpled-header-title-row"><span class="tpled-header-title">Mission Control</span></div>'
      + '<div class="tpled-header-subtitle">Loading read-only readiness summary for ' + esc(group || 'all groups') + '…</div></div>'
      + '</div><div class="mc-loading">Loading Mission Control…</div></div>';
  } else if (!summary && _missionControlLastError) {
    html = '<div class="mission-control-panel"><div class="tpled-header mc-header">'
      + '<div class="tpled-header-copy"><div class="tpled-header-title-row"><span class="tpled-header-title">Mission Control</span></div>'
      + '<div class="tpled-header-subtitle">Read-only readiness summary could not be loaded.</div></div>'
      + '<div class="tpled-header-controls"><button class="tpled-new-btn" onclick="missionControlRefresh()" title="Retry">&#x21BB;</button></div>'
      + '</div><div class="mc-error">' + esc(_missionControlLastError) + '</div></div>';
  } else if (summary) {
    html = _missionControlShellHtml(summary);
  } else {
    html = '<div class="mission-control-panel"><div class="mc-empty-state">Mission Control has not loaded yet.</div></div>';
  }
  panel.innerHTML = html;
  if (typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(panel, snapshot);
  }
}

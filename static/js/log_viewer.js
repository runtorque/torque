/* In-app Torque log viewer. */
var _logViewerState = {
  target: 'daemon',
  cursor: 0,
  lines: [],
  timer: 0,
  follow: true,
  level: '',
  search: '',
};

function _logViewerNormalizeTarget(target) {
  target = String(target || 'daemon').trim().toLowerCase();
  return target === 'supervisor' ? 'supervisor' : 'daemon';
}

function _ensureLogViewerModal() {
  var existing = document.getElementById('modal-log-viewer');
  if (existing) return existing;
  var overlay = document.createElement('div');
  overlay.className = 'overlay';
  overlay.id = 'modal-log-viewer';
  overlay.innerHTML = ''
    + '<div class="modal ui-modal ui-modal--xl ui-modal--tall ui-modal--structured log-viewer-modal" role="dialog" aria-modal="true" aria-labelledby="log-viewer-title">'
    + '  <div class="log-viewer-header ui-modal__header ui-modal__header--bordered">'
    + '    <h2 id="log-viewer-title" class="ui-modal__title">Torque Logs</h2>'
    + '  </div>'
    + '  <div class="log-viewer-body ui-modal__body">'
    + '  <div class="log-viewer-toolbar">'
    + '    <div class="segmented-control ui-tablist log-viewer-targets" role="tablist" aria-label="Log target" onkeydown="uiTablistKeydown(event)">'
    + '      <button id="log-viewer-target-daemon" class="segmented-control__item log-viewer-target active" type="button" role="tab" aria-selected="true" tabindex="0" onclick="_logViewerSetTarget(\'daemon\')">Daemon</button>'
    + '      <button id="log-viewer-target-supervisor" class="segmented-control__item log-viewer-target" type="button" role="tab" aria-selected="false" tabindex="-1" onclick="_logViewerSetTarget(\'supervisor\')">Supervisor</button>'
    + '    </div>'
    + '    <label>Level <select id="log-viewer-level" onchange="_logViewerSetLevel(this.value)">'
    + '      <option value="">All</option><option>DEBUG</option><option>INFO</option><option>WARNING</option><option>ERROR</option>'
    + '    </select></label>'
    + '    <label>Search <input id="log-viewer-search" placeholder="regex or text" oninput="_logViewerSetSearch(this.value)"></label>'
    + '    <label class="log-viewer-follow"><input id="log-viewer-follow" type="checkbox" checked onchange="_logViewerSetFollow(this.checked)"> Follow</label>'
    + '  </div>'
    + '  <div id="log-viewer-list" class="log-viewer-list" role="log" aria-live="polite"></div>'
    + '  </div>'
    + '  <div class="log-viewer-actions ui-modal__footer">'
    + '    <button class="btn-secondary" type="button" onclick="nativeApi && nativeApi.revealLogDir && nativeApi.revealLogDir()">Reveal Folder</button>'
    + '    <button class="btn-cancel" type="button" onclick="closeLogViewer()">Close</button>'
    + '  </div>'
    + '</div>';
  overlay.addEventListener('click', function(event) {
    if (event.target === overlay) closeLogViewer();
  });
  document.body.appendChild(overlay);
  _logViewerSyncTargetControls();
  return overlay;
}

function _logViewerSyncTargetControls() {
  var target = _logViewerNormalizeTarget(_logViewerState.target);
  ['daemon', 'supervisor'].forEach(function(name) {
    var el = document.getElementById('log-viewer-target-' + name);
    if (!el) return;
    var active = name === target;
    if (el.classList) el.classList.toggle('active', active);
    if (typeof el.setAttribute === 'function') el.setAttribute('aria-selected', active ? 'true' : 'false');
    if (typeof el.setAttribute === 'function') el.setAttribute('tabindex', active ? '0' : '-1');
  });
}

function _logViewerSetTarget(target) {
  var next = _logViewerNormalizeTarget(target);
  if (_logViewerState.target === next) return;
  _logViewerState.target = next;
  _logViewerState.cursor = 0;
  _logViewerState.lines = [];
  _logViewerSyncTargetControls();
  _renderLogViewerLines();
  if (_logViewerState.timer && typeof clearTimeout === 'function') clearTimeout(_logViewerState.timer);
  _logViewerState.timer = 0;
  var modal = document.getElementById('modal-log-viewer');
  if (modal && modal.classList && modal.classList.contains('visible')) _pollLogViewer();
}

function _logViewerSetLevel(level) {
  _logViewerState.level = String(level || '');
  _renderLogViewerLines();
}

function _logViewerSetSearch(search) {
  _logViewerState.search = String(search || '');
  _renderLogViewerLines();
}

function _logViewerSetFollow(follow) {
  _logViewerState.follow = !!follow;
  _scheduleLogViewerPoll(0);
}

function _logViewerMatches(line) {
  if (_logViewerState.level && line.level !== _logViewerState.level) return false;
  var needle = _logViewerState.search.trim();
  if (!needle) return true;
  var haystack = (line.raw || line.message || '').toString();
  try {
    return new RegExp(needle, 'i').test(haystack);
  } catch (_) {
    return haystack.toLowerCase().indexOf(needle.toLowerCase()) >= 0;
  }
}

function _renderLogViewerLines() {
  var list = document.getElementById('log-viewer-list');
  if (!list) return;
  var scrollTop = Number(list.scrollTop || 0);
  var filtered = _logViewerState.lines.filter(_logViewerMatches);
  if (!filtered.length) {
    list.innerHTML = '<div class="log-viewer-empty ui-state ui-state--empty ui-state--compact">No log lines match the current filters.</div>';
    if (!_logViewerState.follow) list.scrollTop = scrollTop;
    return;
  }
  list.innerHTML = filtered.map(function(line) {
    var level = esc(line.level || '');
    var message = esc(line.message || line.raw || '');
    var time = '';
    if (line.ts) {
      var d = new Date(line.ts * 1000);
      if (!Number.isNaN(d.getTime())) time = d.toLocaleTimeString();
    }
    return '<div class="log-line log-level-' + level.toLowerCase() + '">'
      + '<span class="log-line-time">' + esc(time) + '</span>'
      + '<span class="log-line-level">' + level + '</span>'
      + '<span class="log-line-message">' + message + '</span>'
      + '</div>';
  }).join('');
  if (_logViewerState.follow) list.scrollTop = list.scrollHeight;
  else list.scrollTop = scrollTop;
}

function _pollLogViewer() {
  var target = _logViewerNormalizeTarget(_logViewerState.target);
  var url = '/logs?target=' + encodeURIComponent(target)
    + '&since=' + encodeURIComponent(String(_logViewerState.cursor || 0))
    + '&follow=' + (_logViewerState.follow ? '1' : '0')
    + '&limit=500';
  fetch(url)
    .then(function(response) { return response.json(); })
    .then(function(payload) {
      if (_logViewerNormalizeTarget(_logViewerState.target) !== target) return;
      if (payload && payload.target && _logViewerNormalizeTarget(payload.target) !== target) return;
      var lines = Array.isArray(payload.lines) ? payload.lines : [];
      if (lines.length) {
        _logViewerState.lines = _logViewerState.lines.concat(lines).slice(-2000);
      }
      if (payload && Object.prototype.hasOwnProperty.call(payload, 'cursor')) {
        _logViewerState.cursor = payload.cursor || 0;
      }
      _renderLogViewerLines();
      _scheduleLogViewerPoll(_logViewerState.follow ? 2000 : 0);
    })
    .catch(function(error) {
      if (typeof console !== 'undefined' && console.warn) console.warn('log viewer poll failed', error);
      _scheduleLogViewerPoll(4000);
    });
}

function _scheduleLogViewerPoll(delay) {
  if (_logViewerState.timer && typeof clearTimeout === 'function') clearTimeout(_logViewerState.timer);
  _logViewerState.timer = 0;
  if (!document.getElementById('modal-log-viewer') || !document.getElementById('modal-log-viewer').classList.contains('visible')) return;
  if (delay == null) delay = 2000;
  if (delay <= 0) {
    if (_logViewerState.follow) _logViewerState.timer = setTimeout(_pollLogViewer, 0);
    return;
  }
  _logViewerState.timer = setTimeout(_pollLogViewer, delay);
}

function openLogViewer(target) {
  var modal = _ensureLogViewerModal();
  modal.classList.add('visible');
  var nextTarget = _logViewerNormalizeTarget(target || _logViewerState.target);
  if (_logViewerState.target !== nextTarget) {
    _logViewerState.target = nextTarget;
    _logViewerState.cursor = 0;
    _logViewerState.lines = [];
  } else if (!_logViewerState.lines.length) {
    _logViewerState.cursor = 0;
  }
  _logViewerSyncTargetControls();
  _pollLogViewer();
}

function closeLogViewer() {
  var modal = document.getElementById('modal-log-viewer');
  if (modal) modal.classList.remove('visible');
  if (_logViewerState.timer && typeof clearTimeout === 'function') clearTimeout(_logViewerState.timer);
  _logViewerState.timer = 0;
}

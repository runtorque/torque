/* Keyboard shortcuts cheatsheet modal. */
var _cheatsheetData = null;
var _cheatsheetSearch = '';

function _ensureCheatsheetModal() {
  var existing = document.getElementById('modal-cheatsheet');
  if (existing) return existing;
  var overlay = document.createElement('div');
  overlay.className = 'overlay';
  overlay.id = 'modal-cheatsheet';
  overlay.innerHTML = ''
    + '<div class="modal modal-wide modal-tall cheatsheet-modal">'
    + '  <div class="cheatsheet-header">'
    + '    <h2>Keyboard Shortcuts</h2>'
    + '    <button class="btn-cancel" type="button" onclick="closeCheatsheet()">Close</button>'
    + '  </div>'
    + '  <input id="cheatsheet-search" class="cheatsheet-search" placeholder="Search shortcuts" oninput="_cheatsheetSetSearch(this.value)">'
    + '  <div id="cheatsheet-content" class="cheatsheet-content"></div>'
    + '</div>';
  overlay.addEventListener('click', function(event) {
    if (event.target === overlay) closeCheatsheet();
  });
  document.body.appendChild(overlay);
  return overlay;
}

function _cheatsheetPlatform() {
  var platform = (navigator && (navigator.userAgentData && navigator.userAgentData.platform || navigator.platform)) || '';
  return /mac/i.test(platform) ? 'mac' : 'other';
}

function _displayShortcut(accelerator) {
  var text = String(accelerator || '').trim();
  if (!text) return '';
  var mac = _cheatsheetPlatform() === 'mac';
  var parts = text.split('+').map(function(part) {
    var key = part.trim();
    if (key === 'CmdOrCtrl') return mac ? '⌘' : 'Ctrl';
    if (key === 'Cmd') return mac ? '⌘' : 'Ctrl';
    if (key === 'Ctrl') return mac ? '⌃' : 'Ctrl';
    if (key === 'Alt') return mac ? '⌥' : 'Alt';
    if (key === 'Shift') return mac ? '⇧' : 'Shift';
    if (key === '/') return '/';
    return key;
  });
  return parts.join(mac ? '' : '+');
}

function _cheatsheetSetSearch(value) {
  _cheatsheetSearch = String(value || '');
  _renderCheatsheet();
}

function _cheatsheetMatches(item) {
  var q = _cheatsheetSearch.trim().toLowerCase();
  if (!q) return true;
  return [item.title, item.description, item.accelerator].some(function(value) {
    return String(value || '').toLowerCase().indexOf(q) >= 0;
  });
}

function _renderCheatsheet() {
  var root = document.getElementById('cheatsheet-content');
  if (!root) return;
  if (!_cheatsheetData) {
    root.innerHTML = '<div class="cheatsheet-empty">Loading shortcuts…</div>';
    return;
  }
  var html = '';
  (_cheatsheetData.categories || []).forEach(function(category) {
    var items = (category.items || []).filter(_cheatsheetMatches);
    if (!items.length) return;
    html += '<section class="cheatsheet-section"><h3>' + esc(category.title || '') + '</h3>';
    items.forEach(function(item) {
      html += '<div class="cheatsheet-row">'
        + '<div><div class="cheatsheet-title">' + esc(item.title || '') + '</div>'
        + '<div class="cheatsheet-desc">' + esc(item.description || '') + '</div></div>'
        + '<kbd>' + esc(_displayShortcut(item.accelerator || '')) + '</kbd>'
        + '</div>';
    });
    html += '</section>';
  });
  root.innerHTML = html || '<div class="cheatsheet-empty">No shortcuts match.</div>';
}

function _loadCheatsheetData() {
  if (_cheatsheetData) return Promise.resolve(_cheatsheetData);
  return fetch('/static/cheatsheet/cheatsheet.json')
    .then(function(response) { return response.json(); })
    .then(function(data) { _cheatsheetData = data; return data; });
}

function openCheatsheet() {
  var modal = _ensureCheatsheetModal();
  modal.classList.add('visible');
  _renderCheatsheet();
  _loadCheatsheetData().then(_renderCheatsheet).catch(function(error) {
    if (typeof console !== 'undefined' && console.warn) console.warn('cheatsheet load failed', error);
  });
  var search = document.getElementById('cheatsheet-search');
  if (search && typeof search.focus === 'function') setTimeout(function() { search.focus(); }, 0);
}

function closeCheatsheet() {
  var modal = document.getElementById('modal-cheatsheet');
  if (modal) modal.classList.remove('visible');
}

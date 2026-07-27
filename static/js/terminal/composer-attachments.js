/* Terminal module: composer attachments. */

function _terminalComposeDroppedFiles(dataTransfer) {
  if (!dataTransfer || !dataTransfer.files || !dataTransfer.files.length) return [];
  return Array.prototype.slice.call(dataTransfer.files);
}

function _terminalComposeHasDraggedFiles(dataTransfer) {
  if (!dataTransfer) return false;
  if (dataTransfer.types && typeof dataTransfer.types.length === 'number') {
    if (Array.prototype.indexOf.call(dataTransfer.types, 'Files') !== -1) return true;
  }
  if (dataTransfer.items && typeof dataTransfer.items.length === 'number') {
    for (var i = 0; i < dataTransfer.items.length; i++) {
      var item = dataTransfer.items[i];
      if (item && item.kind === 'file') return true;
    }
  }
  return _terminalComposeDroppedFiles(dataTransfer).length > 0;
}

function _terminalComposeNormalizeMime(type) {
  return String(type || '').split(';')[0].trim().toLowerCase();
}

function _terminalComposeFormatBytes(bytes) {
  var mb = Math.floor(bytes / (1024 * 1024));
  return (mb || 1) + ' MB';
}

function _terminalComposeFileLabel(file) {
  return file && file.name ? String(file.name) : 'Dropped file';
}

function terminalComposeValidateDroppedFiles(files) {
  var accepted = [];
  var errors = [];
  var list = Array.prototype.slice.call(files || []);
  for (var i = 0; i < list.length; i++) {
    var file = list[i];
    var mime = _terminalComposeNormalizeMime(file && file.type);
    var name = _terminalComposeFileLabel(file);
    if (!TERMINAL_COMPOSE_ATTACHMENT_MIME_TYPES[mime]) {
      errors.push(name + ' is not a supported image type.');
      continue;
    }
    if (file && typeof file.size === 'number'
        && file.size > TERMINAL_COMPOSE_ATTACHMENT_MAX_BYTES) {
      errors.push(name + ' is larger than '
        + _terminalComposeFormatBytes(TERMINAL_COMPOSE_ATTACHMENT_MAX_BYTES) + '.');
      continue;
    }
    accepted.push(file);
  }
  return { accepted: accepted, errors: errors };
}

function _terminalComposeAttachmentToken(number) {
  return '[ Image #' + number + ' ]';
}

function _terminalComposeAttachmentLabel(entry) {
  if (!entry) return 'Image';
  const filename = String(entry.filename || '').trim();
  if (filename) return filename;
  const path = String(entry.path || '').trim();
  if (path) return path.replace(/\\/g, '/').split('/').pop() || 'Image';
  return 'Image';
}

function _terminalComposeAttachmentPreviewUrl(entry) {
  if (!entry) return '';
  return String(entry.preview_url || entry.previewUrl || '').trim();
}

function _terminalComposeMakePreviewUrl(file) {
  const urlApi = (typeof URL !== 'undefined' && URL) || (
    typeof window !== 'undefined' && window ? window.URL : null
  );
  if (urlApi && typeof urlApi.createObjectURL === 'function') {
    try {
      return urlApi.createObjectURL(file);
    } catch (_e) {
      return '';
    }
  }
  return '';
}

function _terminalComposeRevokePreviewUrl(url) {
  const value = String(url || '');
  if (!value || value.indexOf('blob:') !== 0) return;
  const urlApi = (typeof URL !== 'undefined' && URL) || (
    typeof window !== 'undefined' && window ? window.URL : null
  );
  if (urlApi && typeof urlApi.revokeObjectURL === 'function') {
    try { urlApi.revokeObjectURL(value); } catch (_e) {}
  }
}

function _terminalComposeSortedAttachments(cellId) {
  const stateForCell = _terminalComposeAttachments[String(cellId || '')];
  const entries = stateForCell && Array.isArray(stateForCell.entries)
    ? stateForCell.entries.slice()
    : [];
  entries.sort(function(a, b) {
    const ap = Number(a && a.position);
    const bp = Number(b && b.position);
    const at = Number(String((a && a.token) || '').match(/\d+/));
    const bt = Number(String((b && b.token) || '').match(/\d+/));
    if ((Number.isFinite(ap) ? ap : 0) !== (Number.isFinite(bp) ? bp : 0)) {
      return (Number.isFinite(ap) ? ap : 0) - (Number.isFinite(bp) ? bp : 0);
    }
    return (Number.isFinite(at) ? at : 0) - (Number.isFinite(bt) ? bt : 0);
  });
  return entries;
}

function _terminalComposeAttachmentIndex(cellId, token) {
  const entries = _terminalComposeSortedAttachments(cellId);
  const needle = String(token || '');
  for (let i = 0; i < entries.length; i++) {
    if (String(entries[i].token || '') === needle) return i;
  }
  return -1;
}

function _terminalComposeRenderAttachmentChips(form, cellId, opts) {
  if (!form || typeof form.querySelector !== 'function') return;
  const input = form.querySelector('.terminal-compose-input');
  if (_terminalComposeIsRichInput(input)) {
    // While the contenteditable composer owns focus, the browser's live DOM is
    // the source of truth for newline/caret geometry. Generic workspace
    // rerenders happen under active terminal output; rewriting innerHTML here
    // canonicalizes browser-created paragraphs/BRs on every keyup and was the
    // root of multiline draft collapse + visible editor/terminal flicker. Real
    // attachment operations call this helper without the preserve flag, so
    // PR #782 inline-token rendering still updates immediately.
    if (!(opts && opts.preserveActiveRichEditor && _terminalComposeOwnsLiveEditing(input))) {
      _terminalComposeRenderRichInput(input, { preserveSelection: true });
    }
    return;
  }
  const row = form.querySelector('.terminal-compose-attachments');
  if (!row) return;
  const entries = _terminalComposeSortedAttachments(cellId);
  if (!entries.length) {
    row.innerHTML = '';
    row.hidden = true;
    return;
  }
  const selected = String(_terminalComposeSelectedAttachmentByCell[String(cellId || '')] || '');
  let html = '';
  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i] || {};
    const token = String(entry.token || '');
    const label = _terminalComposeAttachmentLabel(entry);
    const title = (entry.path ? String(entry.path) : label);
    html += '<button type="button" class="terminal-compose-attachment-chip'
      + (selected && selected === token ? ' selected' : '')
      + '" data-attachment-token="' + esc(token) + '"'
      + ' onclick="return terminalComposeAttachmentPreview(event, \'' + esc(cellId).replace(/'/g, "\\'") + '\', \'' + esc(token).replace(/'/g, "\\'") + '\')"'
      + ' onkeydown="return terminalComposeAttachmentChipKeydown(event, \'' + esc(cellId).replace(/'/g, "\\'") + '\', \'' + esc(token).replace(/'/g, "\\'") + '\')"'
      + ' title="' + esc(title) + '" aria-label="Preview attached image ' + esc(label) + '">'
      + '<span class="terminal-compose-attachment-icon" aria-hidden="true">▧</span>'
      + '<span class="terminal-compose-attachment-label">' + esc(label) + '</span>'
      + '</button>';
  }
  row.hidden = false;
  if (row.innerHTML !== html) row.innerHTML = html;
}

function _terminalComposeRefreshAttachmentChips(cellId) {
  const id = String(cellId || '');
  const input = document.getElementById ? document.getElementById(_terminalComposeInputId(id)) : null;
  const form = input ? _terminalComposeContainerFor(input) : null;
  if (input && _terminalComposeIsRichInput(input)) {
    _terminalComposeRenderRichInput(input, { preserveSelection: true });
  } else if (form) _terminalComposeRenderAttachmentChips(form, id);
}

function _terminalComposeAttachmentState(cellId) {
  var id = String(cellId || '');
  if (!id) return null;
  if (!_terminalComposeAttachments[id]) {
    _terminalComposeAttachments[id] = { next: 1, entries: [] };
  }
  var stateForCell = _terminalComposeAttachments[id];
  if (!Array.isArray(stateForCell.entries)) stateForCell.entries = [];
  var next = Number(stateForCell.next || 1);
  stateForCell.next = Number.isFinite(next) && next > 0 ? Math.floor(next) : 1;
  return stateForCell;
}

function _terminalComposeHighestAttachmentTokenNumber(text) {
  var highest = 0;
  var re = /\[ Image #(\d+) \]/g;
  var match = null;
  text = String(text || '');
  while ((match = re.exec(text))) {
    highest = Math.max(highest, Number(match[1]) || 0);
  }
  return highest;
}

function _terminalComposeAdjustAttachmentPositions(cellId, oldText, newText) {
  const id = String(cellId || '');
  const stateForCell = id ? _terminalComposeAttachments[id] : null;
  if (!stateForCell || !Array.isArray(stateForCell.entries)) return;
  oldText = String(oldText || '');
  newText = String(newText || '');
  if (oldText === newText) return;
  let prefix = 0;
  const prefixMax = Math.min(oldText.length, newText.length);
  while (prefix < prefixMax && oldText[prefix] === newText[prefix]) prefix += 1;
  let oldSuffix = oldText.length;
  let newSuffix = newText.length;
  while (oldSuffix > prefix
      && newSuffix > prefix
      && oldText[oldSuffix - 1] === newText[newSuffix - 1]) {
    oldSuffix -= 1;
    newSuffix -= 1;
  }
  const delta = newText.length - oldText.length;
  for (let i = 0; i < stateForCell.entries.length; i++) {
    const entry = stateForCell.entries[i];
    if (!entry) continue;
    let pos = Number(entry.position);
    if (!Number.isFinite(pos)) pos = oldText.length;
    if (pos >= oldSuffix) pos += delta;
    else if (pos >= prefix) pos = prefix;
    entry.position = Math.max(0, Math.min(newText.length, Math.floor(pos)));
  }
}

function _terminalComposeRegisterAttachmentEntries(cellId, entries, displayText, position) {
  var stateForCell = _terminalComposeAttachmentState(cellId);
  if (!stateForCell) {
    return [];
  }
  stateForCell.next = Math.max(
    stateForCell.next || 1,
    _terminalComposeHighestAttachmentTokenNumber(displayText) + 1
  );
  var registered = [];
  var text = String(displayText || '');
  var insertAt = Math.max(0, Math.min(text.length, Number(position) || 0));
  for (var i = 0; i < (entries || []).length; i++) {
    var raw = entries[i] || {};
    var path = String(raw.path || '');
    if (!path) continue;
    var token = _terminalComposeAttachmentToken(stateForCell.next);
    stateForCell.next += 1;
    var entry = {
      token: token,
      path: path,
      filename: String(raw.filename || '').trim(),
      mime_type: String(raw.mime_type || raw.mime || '').trim(),
      size_bytes: Number(raw.size_bytes || raw.size || 0) || 0,
      preview_url: String(raw.preview_url || raw.previewUrl || '').trim(),
      position: insertAt,
    };
    stateForCell.entries.push(entry);
    registered.push(entry);
  }
  return registered;
}

function _terminalComposePruneAttachments(cellId, displayText) {
  var id = String(cellId || '');
  var stateForCell = id ? _terminalComposeAttachments[id] : null;
  if (!stateForCell || !Array.isArray(stateForCell.entries)) return;
  var text = String(displayText || '');
  stateForCell.entries.forEach(function(entry) {
    if (!entry) return;
    var pos = Number(entry.position);
    entry.position = Math.max(0, Math.min(text.length, Number.isFinite(pos) ? Math.floor(pos) : text.length));
  });
  if (!stateForCell.entries.length) delete _terminalComposeAttachments[id];
}

function _terminalComposePayloadText(cellId, displayText) {
  var id = String(cellId || '');
  var text = String(displayText || '');
  if (!id || !_terminalComposeAttachments[id]) return text;
  _terminalComposePruneAttachments(id, text);
  var entries = _terminalComposeSortedAttachments(id);
  var groups = [];
  for (var i = 0; i < entries.length; i++) {
    var entry = entries[i];
    if (!entry || !entry.path) continue;
    var pos = Number(entry.position);
    pos = Math.max(0, Math.min(text.length, Number.isFinite(pos) ? Math.floor(pos) : text.length));
    var group = groups.length ? groups[groups.length - 1] : null;
    if (!group || group.position !== pos) {
      group = { position: pos, paths: [] };
      groups.push(group);
    }
    group.paths.push(String(entry.path || ''));
  }
  var out = '';
  var cursor = 0;
  for (var gi = 0; gi < groups.length; gi++) {
    var g = groups[gi];
    out += text.slice(cursor, g.position);
    out += g.paths.join('\n');
    cursor = g.position;
    // Attachment tokens have no logical text width. Renderers provide their
    // own one-space caret host, so mirror that boundary in the outgoing
    // message when real text immediately follows without inventing a trailing
    // space for an image-only draft.
    var nextPosition = gi + 1 < groups.length ? groups[gi + 1].position : text.length;
    var following = text.slice(cursor, nextPosition);
    if (following && !/^\s/.test(following)) out += ' ';
  }
  out += text.slice(cursor);
  return out;
}

function _terminalComposeInsertAttachments(input, entries) {
  if (!input || !entries || !entries.length) return;
  var cellId = input.dataset ? (input.dataset.cellId || '') : '';
  _terminalComposeHistoryPrepare(input, 'attachment');
  var value = _terminalComposeInputText(input);
  var selection = _terminalComposeSelectionOffsets(input);
  var start = selection.start;
  var end = selection.end;
  start = Math.max(0, Math.min(value.length, start));
  end = Math.max(start, Math.min(value.length, end));
  if (end > start) _terminalComposeSetInputText(input, value.slice(0, start) + value.slice(end));
  if (cellId) _terminalComposeDrafts[cellId] = _terminalComposeInputText(input);
  _terminalComposeRegisterAttachmentEntries(
    cellId,
    entries,
    _terminalComposeInputText(input),
    start
  );
  var cursor = start;
  _terminalComposeRenderRichInput(input, { preserveSelection: false });
  if (_terminalComposeIsRichInput(input)) {
    _terminalComposeSetRichSelection(input, cursor, cursor, 'none', { afterAttachments: true });
  } else if (typeof input.setSelectionRange === 'function') {
    input.setSelectionRange(cursor, cursor);
  } else {
    input.selectionStart = cursor;
    input.selectionEnd = cursor;
  }
  terminalComposeInput(input);
  if (typeof input.focus === 'function') input.focus();
}

function _terminalComposeRemoveAttachment(cellId, token) {
  const id = String(cellId || '');
  const stateForCell = id ? _terminalComposeAttachments[id] : null;
  if (!stateForCell || !Array.isArray(stateForCell.entries)) return false;
  const input = document.getElementById ? document.getElementById(_terminalComposeInputId(id)) : null;
  _terminalComposeHistoryPrepare(input, 'attachment');
  const needle = String(token || '');
  let removed = false;
  let removedPosition = 0;
  stateForCell.entries = stateForCell.entries.filter(function(entry) {
    if (!entry || String(entry.token || '') !== needle) return true;
    removedPosition = Math.max(0, Number(entry.position) || 0);
    removed = true;
    return false;
  });
  if (!stateForCell.entries.length) delete _terminalComposeAttachments[id];
  if (_terminalComposeSelectedAttachmentByCell[id] === needle) {
    delete _terminalComposeSelectedAttachmentByCell[id];
  }
  _terminalComposeRefreshAttachmentChips(id);
  if (input) {
    _terminalComposeRenderRichInput(input, { preserveSelection: false });
    if (_terminalComposeIsRichInput(input)) {
      _terminalComposeSetRichSelection(input, removedPosition, removedPosition, 'none');
    }
    _terminalComposeSetButtonState(input);
    if (typeof input.focus === 'function') input.focus();
  }
  if (input) _terminalComposeHistoryCommit(input, 'attachment');
  return removed;
}

function _terminalComposeAttachmentEntry(cellId, token) {
  const entries = _terminalComposeSortedAttachments(cellId);
  const needle = String(token || '');
  for (let i = 0; i < entries.length; i++) {
    if (String(entries[i].token || '') === needle) return entries[i];
  }
  return null;
}

function _terminalComposeAttachmentAtCaret(input, direction) {
  if (!input) return null;
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  const entries = _terminalComposeSortedAttachments(cellId);
  if (!entries.length) return null;
  const caret = _terminalComposeSelectionOffsets(input).start;
  let candidate = null;
  if (direction < 0) {
    for (let i = 0; i < entries.length; i++) {
      const pos = Math.max(0, Number(entries[i].position) || 0);
      if (pos === caret) candidate = entries[i];
      else if (pos < caret) candidate = null;
      else break;
    }
  } else {
    for (let j = 0; j < entries.length; j++) {
      const nextPos = Math.max(0, Number(entries[j].position) || 0);
      if (nextPos === caret) {
        candidate = entries[j];
        break;
      }
      if (nextPos > caret) break;
    }
  }
  return candidate;
}

function _terminalComposeSiblingAttachment(node) {
  if (!node || node.nodeType !== 1 || !node.getAttribute) return null;
  const token = String(node.getAttribute('data-attachment-token') || '');
  return token ? node : null;
}

function _terminalComposeCaretHost(node) {
  for (let current = node; current; current = current.parentNode) {
    if (current.nodeType === 1 && current.getAttribute
        && current.getAttribute('data-attachment-caret-host')) return current;
  }
  return null;
}

function _terminalComposePristineCaretHost(host) {
  if (!host) return false;
  let text = '';
  const children = host.childNodes || [];
  for (let i = 0; i < children.length; i++) {
    const child = children[i];
    if (child && child.nodeType === 3) text += String(child.nodeValue || '');
    else if (child && typeof child.textContent === 'string') text += child.textContent;
  }
  return text.replace(/\u00a0/g, ' ') === ' ';
}

function _terminalComposeAdjacentAttachmentNode(input, direction) {
  if (!_terminalComposeIsRichInput(input)
      || typeof window === 'undefined'
      || !window.getSelection) return null;
  const selection = window.getSelection();
  if (!selection || selection.rangeCount <= 0 || !selection.isCollapsed) return null;
  let node = selection.focusNode;
  let offset = selection.focusOffset;
  if (!node || !(input.contains(node) || node === input)) return null;

  function attachmentAncestor(candidate) {
    for (let current = candidate; current && current !== input; current = current.parentNode) {
      if (_terminalComposeSiblingAttachment(current)) return current;
    }
    return null;
  }
  function deepestLast(candidate) {
    let n = candidate;
    while (n && !attachmentAncestor(n) && n.lastChild) n = n.lastChild;
    return attachmentAncestor(n) || n;
  }
  function deepestFirst(candidate) {
    let n = candidate;
    while (n && !attachmentAncestor(n) && n.firstChild) n = n.firstChild;
    return attachmentAncestor(n) || n;
  }
  function previousCandidate(n, off) {
    if (n.nodeType === 3) {
      if (off > 0) return null;
      return n.previousSibling ? deepestLast(n.previousSibling) : previousFromParent(n.parentNode);
    }
    const children = n.childNodes || [];
    if (off > 0) return deepestLast(children[off - 1]);
    return previousFromParent(n);
  }
  function previousFromParent(n) {
    if (!n || n === input) return null;
    if (n.previousSibling) return deepestLast(n.previousSibling);
    return previousFromParent(n.parentNode);
  }
  function nextCandidate(n, off) {
    if (n.nodeType === 3) {
      if (off < String(n.nodeValue || '').length) return null;
      return n.nextSibling ? deepestFirst(n.nextSibling) : nextFromParent(n.parentNode);
    }
    const children = n.childNodes || [];
    if (off < children.length) return deepestFirst(children[off]);
    return nextFromParent(n);
  }
  function nextFromParent(n) {
    if (!n || n === input) return null;
    if (n.nextSibling) return deepestFirst(n.nextSibling);
    return nextFromParent(n.parentNode);
  }
  let candidate = direction < 0 ? previousCandidate(node, offset) : nextCandidate(node, offset);
  while (candidate) {
    if (candidate.nodeType === 3 && !String(candidate.nodeValue || '').length) {
      candidate = direction < 0
        ? (candidate.previousSibling ? deepestLast(candidate.previousSibling) : previousFromParent(candidate.parentNode))
        : (candidate.nextSibling ? deepestFirst(candidate.nextSibling) : nextFromParent(candidate.parentNode));
      continue;
    }
    if (candidate.nodeType === 1) {
      if (_terminalComposeSiblingAttachment(candidate)) return candidate;
      const chip = candidate.closest ? candidate.closest('.terminal-compose-attachment-chip') : null;
      if (chip && input.contains(chip)) return chip;
    }
    // Only Backspace may cross the untouched one-space host that follows a
    // chip. Delete after that host is deliberately ordinary text deletion,
    // never reverse-direction token deletion.
    const host = _terminalComposeCaretHost(candidate);
    if (direction < 0 && _terminalComposePristineCaretHost(host)) {
      candidate = host.previousSibling
        ? deepestLast(host.previousSibling)
        : previousFromParent(host.parentNode);
      continue;
    }
    return null;
  }
  return null;
}

function _terminalComposeHandleAttachmentDeleteKey(evt, cellId) {
  if (!evt || (evt.key !== 'Backspace' && evt.key !== 'Delete')) return false;
  if (evt.metaKey || evt.ctrlKey || evt.altKey) return false;
  const input = evt.target && typeof evt.target.value === 'string'
    ? evt.target
    : (document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null);
  if (!input) return false;
  const id = String(cellId || (input.dataset ? input.dataset.cellId : '') || '');
  const selection = _terminalComposeSelectionOffsets(input);
  if (selection.start !== selection.end) return false;
  // Logical attachment positions are intentionally zero-width, so position
  // arithmetic cannot tell which side of a token the caret occupies. Require
  // a real rich-DOM adjacency instead; preview selection must never influence
  // destructive keyboard behavior elsewhere in the draft.
  const richNode = _terminalComposeAdjacentAttachmentNode(input, evt.key === 'Backspace' ? -1 : 1);
  const richToken = richNode && richNode.getAttribute
    ? richNode.getAttribute('data-attachment-token')
    : '';
  const entry = richToken ? _terminalComposeAttachmentEntry(id, richToken) : null;
  if (!entry) return false;
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
  return _terminalComposeRemoveAttachment(id, entry.token);
}

function closeTerminalComposeAttachmentPreview() {
  if (_terminalComposePreviewKeyHandler) {
    document.removeEventListener('keydown', _terminalComposePreviewKeyHandler, true);
    _terminalComposePreviewKeyHandler = null;
  }
  if (_terminalComposePreviewOverlay) {
    if (typeof _terminalComposePreviewOverlay.remove === 'function') {
      _terminalComposePreviewOverlay.remove();
    } else if (_terminalComposePreviewOverlay.parentNode) {
      _terminalComposePreviewOverlay.parentNode.removeChild(_terminalComposePreviewOverlay);
    }
    _terminalComposePreviewOverlay = null;
  }
}

function _terminalComposeOpenAttachmentPreview(entry) {
  if (!entry || typeof document === 'undefined' || !document.createElement) return false;
  closeTerminalComposeAttachmentPreview();
  const overlay = document.createElement('div');
  overlay.id = 'modal-terminal-compose-attachment-preview';
  overlay.className = 'overlay terminal-compose-attachment-preview-overlay visible';
  if (overlay.classList) {
    overlay.classList.add('overlay', 'terminal-compose-attachment-preview-overlay', 'visible');
  }
  const label = _terminalComposeAttachmentLabel(entry);
  const url = _terminalComposeAttachmentPreviewUrl(entry);
  overlay.innerHTML = ''
    + '<div class="modal ui-modal ui-modal--lg ui-modal--structured terminal-compose-attachment-preview-modal" role="dialog" aria-modal="true"'
    + ' aria-label="Attached image preview">'
    + '  <div class="terminal-compose-attachment-preview-head ui-modal__header ui-modal__header--bordered">'
    + '    <div class="terminal-compose-attachment-preview-title ui-modal__title">' + esc(label) + '</div>'
    + '    <button type="button" class="terminal-compose-attachment-preview-close"'
    + ' onclick="closeTerminalComposeAttachmentPreview()" aria-label="Close">&times;</button>'
    + '  </div>'
    + '  <div class="terminal-compose-attachment-preview-body ui-modal__body ui-modal__body--flush">'
    + (url
      ? '<img class="terminal-compose-attachment-preview-image" src="' + esc(url) + '" alt="' + esc(label) + '">'
      : '<div class="terminal-compose-attachment-preview-unavailable">Preview unavailable for this image in the current session.</div>')
    + '  </div>'
    + '</div>';
  overlay.addEventListener('mousedown', function(e) {
    if (e.target === overlay) closeTerminalComposeAttachmentPreview();
  });
  _terminalComposePreviewKeyHandler = function(e) {
    if (!e || e.key !== 'Escape') return;
    if (e.preventDefault) e.preventDefault();
    if (e.stopPropagation) e.stopPropagation();
    closeTerminalComposeAttachmentPreview();
  };
  document.addEventListener('keydown', _terminalComposePreviewKeyHandler, true);
  document.body.appendChild(overlay);
  _terminalComposePreviewOverlay = overlay;
  const closeButton = overlay.querySelector
    ? overlay.querySelector('.terminal-compose-attachment-preview-close')
    : null;
  if (closeButton && typeof closeButton.focus === 'function') closeButton.focus();
  return false;
}

function terminalComposeAttachmentPreview(evt, cellId, token) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  const id = String(cellId || '');
  const needle = String(token || '');
  if (id && needle) _terminalComposeSelectedAttachmentByCell[id] = needle;
  _terminalComposeRefreshAttachmentChips(id);
  const entry = _terminalComposeAttachmentEntry(id, needle);
  return _terminalComposeOpenAttachmentPreview(entry);
}

function terminalComposeAttachmentChipKeydown(evt, cellId, token) {
  if (!evt) return true;
  const key = evt.key || evt.code;
  if (key === 'Backspace' || key === 'Delete') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    _terminalComposeRemoveAttachment(cellId, token);
    return false;
  }
  if (key === 'Enter' || key === ' ') {
    return terminalComposeAttachmentPreview(evt, cellId, token);
  }
  return true;
}

async function _terminalComposeUploadAttachments(cellId, files) {
  var fd = new FormData();
  fd.append('agent_id', String(cellId || ''));
  for (var i = 0; i < files.length; i++) {
    fd.append('file', files[i]);
  }
  var r = await fetch('/api/attachment/upload', { method: 'POST', body: fd });
  var res = r && typeof r.json === 'function' ? await r.json() : null;
  if (!res || !res.ok) {
    throw new Error((res && res.error) || 'Attachment upload failed.');
  }
  if (!Array.isArray(res.data)) {
    throw new Error('Attachment upload failed.');
  }
  return res.data
    .map(function(entry, index) {
      if (!entry || !entry.path) return null;
      var file = files[index] || null;
      return Object.assign({}, entry, {
        filename: entry.filename || (file && file.name) || '',
        mime_type: entry.mime_type || (file && file.type) || '',
        size_bytes: entry.size_bytes || (file && file.size) || 0,
        preview_url: _terminalComposeMakePreviewUrl(file),
      });
    })
    .filter(Boolean);
}

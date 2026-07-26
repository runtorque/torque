/* Terminal module: composer. */

function _terminalComposeDomId(cellId) {
  const safe = String(cellId || '')
    .replace(/[^A-Za-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return safe || 'selected';
}

function _terminalComposeInputId(cellId) {
  return 'terminal-compose-input-' + _terminalComposeDomId(cellId);
}

function _terminalComposeButtonId(cellId) {
  return 'terminal-compose-submit-' + _terminalComposeDomId(cellId);
}

function _terminalComposeHistoryButtonId(cellId) {
  return 'terminal-compose-history-' + _terminalComposeDomId(cellId);
}

function _terminalComposeHistoryMenuId(cellId) {
  return 'terminal-compose-history-menu-' + _terminalComposeDomId(cellId);
}

function _terminalComposeTaskDropdownId(cellId) {
  return 'terminal-compose-task-dropdown-' + _terminalComposeDomId(cellId);
}

function _terminalComposeSlashDropdownId(cellId) {
  return 'terminal-compose-slash-dropdown-' + _terminalComposeDomId(cellId);
}

function _terminalComposeContainerFor(el) {
  for (let node = el; node; node = node.parentNode) {
    if (node.classList && node.classList.contains('terminal-compose')) {
      return node;
    }
  }
  return null;
}

function _terminalComposeTextarea(root) {
  return root && root.querySelector ? root.querySelector('.terminal-compose-input') : null;
}

function _terminalComposeIsRichInput(input) {
  if (!input) return false;
  const editable = typeof input.getAttribute === 'function'
    ? input.getAttribute('contenteditable')
    : '';
  return editable === 'true';
}

function _terminalComposeOwnsLiveEditing(input) {
  return !!(
    input
    && _terminalComposeIsRichInput(input)
    && typeof document !== 'undefined'
    && document.activeElement === input
  );
}

function _terminalComposeEscapeText(value) {
  return esc(String(value || '')).replace(/\n/g, '<br>');
}

function _terminalComposeNodeTextLength(node) {
  if (!node) return 0;
  if (node.nodeType === 3) return String(node.nodeValue || '').length;
  if (node.nodeType !== 1) return 0;
  if (node.getAttribute && node.getAttribute('data-attachment-token')) return 0;
  // The space after an atomic attachment is a real, editable DOM boundary,
  // but not draft text. Keeping it out of logical offsets lets attachment
  // positions remain zero-width while still giving browsers somewhere stable
  // to put a caret after a chip.
  if (node.getAttribute && node.getAttribute('data-attachment-caret-host')) return 0;
  if (String(node.nodeName || '').toUpperCase() === 'BR') return 1;
  let total = 0;
  const children = node.childNodes || [];
  for (let i = 0; i < children.length; i++) {
    total += _terminalComposeNodeTextLength(children[i]);
  }
  return total;
}

function _terminalComposeRichHtml(cellId, text) {
  const entries = _terminalComposeSortedAttachments(cellId);
  const value = String(text || '');
  let html = '';
  let cursor = 0;
  for (let i = 0; i < entries.length;) {
    const group = [];
    const first = entries[i] || {};
    let pos = Number(first.position);
    pos = Math.max(0, Math.min(value.length, Number.isFinite(pos) ? Math.floor(pos) : value.length));
    while (i < entries.length) {
      const entry = entries[i] || {};
      const entryPos = Number(entry.position);
      const normalizedPos = Math.max(0, Math.min(
        value.length,
        Number.isFinite(entryPos) ? Math.floor(entryPos) : value.length
      ));
      if (normalizedPos !== pos) break;
      if (String(entry.token || '')) group.push(entry);
      i += 1;
    }
    if (pos > cursor) {
      html += _terminalComposeEscapeText(value.slice(cursor, pos));
      cursor = pos;
    }
    for (let j = 0; j < group.length; j++) {
      const entry = group[j];
      const token = String(entry.token || '');
      const label = _terminalComposeAttachmentLabel(entry);
      const title = entry.path ? String(entry.path) : label;
      const tokenLabel = token || label;
      const selected = String(_terminalComposeSelectedAttachmentByCell[String(cellId || '')] || '') === token;
      html += '<span class="terminal-compose-attachment-chip terminal-compose-inline-attachment-chip'
        + (selected ? ' selected' : '')
        + '" contenteditable="false" role="button" tabindex="0"'
        + ' data-attachment-token="' + esc(token) + '"'
        + ' onclick="return terminalComposeAttachmentPreview(event, \'' + esc(cellId).replace(/'/g, "\\'") + '\', \'' + esc(token).replace(/'/g, "\\'") + '\')"'
        + ' onkeydown="return terminalComposeAttachmentChipKeydown(event, \'' + esc(cellId).replace(/'/g, "\\'") + '\', \'' + esc(token).replace(/'/g, "\\'") + '\')"'
        + ' title="' + esc(title) + '" aria-label="Preview attached image ' + esc(tokenLabel) + ' (' + esc(label) + ')">'
        + esc(tokenLabel)
        + '</span>';
      // A token needs a durable inline caret boundary. The final token may
      // reuse a following ordinary whitespace character, otherwise emit one
      // real space. This avoids accumulating separators across rerenders.
      if (j < group.length - 1 || !/^\s/.test(value.slice(cursor))) {
        html += '<span class="terminal-compose-attachment-caret-host" data-attachment-caret-host="true"> </span>';
      }
    }
  }
  html += _terminalComposeEscapeText(value.slice(cursor));
  return html;
}

function _terminalComposeSelectionOffsets(input) {
  const value = String(input && input.value || '');
  const fallbackStart = typeof input.selectionStart === 'number' ? input.selectionStart : value.length;
  const fallbackEnd = typeof input.selectionEnd === 'number' ? input.selectionEnd : fallbackStart;
  if (!_terminalComposeIsRichInput(input)
      || typeof window === 'undefined'
      || !window.getSelection
      || !input.contains) {
    return {
      start: Math.max(0, Math.min(value.length, fallbackStart)),
      end: Math.max(0, Math.min(value.length, fallbackEnd)),
      direction: input && input.selectionDirection ? input.selectionDirection : 'none',
    };
  }
  const selection = window.getSelection();
  if (!selection || selection.rangeCount <= 0) {
    return { start: value.length, end: value.length, direction: 'none' };
  }
  const anchorInside = input.contains(selection.anchorNode) || selection.anchorNode === input;
  const focusInside = input.contains(selection.focusNode) || selection.focusNode === input;
  if (!anchorInside || !focusInside) {
    return { start: value.length, end: value.length, direction: 'none' };
  }
  const anchor = _terminalComposeOffsetForNode(input, selection.anchorNode, selection.anchorOffset);
  const focus = _terminalComposeOffsetForNode(input, selection.focusNode, selection.focusOffset);
  return {
    start: Math.min(anchor, focus),
    end: Math.max(anchor, focus),
    direction: focus < anchor ? 'backward' : (focus > anchor ? 'forward' : 'none'),
  };
}

function _terminalComposeOffsetForNode(root, target, targetOffset) {
  let found = false;
  let total = 0;
  function walk(node) {
    if (!node || found) return;
    if (node === target) {
      found = true;
      if (node.nodeType === 3) {
        total += Math.max(0, Math.min(String(node.nodeValue || '').length, Number(targetOffset) || 0));
      } else if (node.nodeType === 1) {
        if (node.getAttribute && node.getAttribute('data-attachment-token')) {
          return;
        }
        const children = node.childNodes || [];
        const count = Math.max(0, Math.min(children.length, Number(targetOffset) || 0));
        for (let i = 0; i < count; i++) total += _terminalComposeNodeTextLength(children[i]);
      }
      return;
    }
    if (node.nodeType === 3) {
      total += String(node.nodeValue || '').length;
      return;
    }
    if (node.nodeType !== 1) return;
    if (node.getAttribute && node.getAttribute('data-attachment-token')) return;
    if (String(node.nodeName || '').toUpperCase() === 'BR') {
      total += 1;
      return;
    }
    const children = node.childNodes || [];
    for (let i = 0; i < children.length; i++) walk(children[i]);
  }
  walk(root);
  return total;
}

function _terminalComposeSetRichSelection(input, start, end, direction, options) {
  if (!_terminalComposeIsRichInput(input)
      || typeof document === 'undefined'
      || !document.createRange
      || typeof window === 'undefined'
      || !window.getSelection) {
    return false;
  }
  const valueLength = String(input.value || '').length;
  start = Math.max(0, Math.min(valueLength, start));
  end = Math.max(0, Math.min(valueLength, end));
  const afterAttachments = !!(options && options.afterAttachments);
  function boundaryFor(pos) {
    let best = { node: input, offset: 0 };
    let consumed = 0;
    const children = input.childNodes || [];
    for (let i = 0; i < children.length; i++) {
      const child = children[i];
      const len = _terminalComposeNodeTextLength(child);
      if (len === 0) {
        if (consumed === pos && afterAttachments
            && child.nodeType === 1
            && child.getAttribute
            && (child.getAttribute('data-attachment-token')
              || child.getAttribute('data-attachment-caret-host'))) {
          best = { node: input, offset: i + 1 };
        }
        continue;
      }
      if (consumed + len === pos) {
        if (!afterAttachments) {
          if (child.nodeType === 3) return { node: child, offset: len };
          return { node: input, offset: i + 1 };
        }
        consumed += len;
        best = { node: input, offset: i + 1 };
        continue;
      }
      if (consumed + len > pos) {
        if (child.nodeType === 3) {
          return { node: child, offset: Math.max(0, Math.min(len, pos - consumed)) };
        }
        if (String(child.nodeName || '').toUpperCase() === 'BR') {
          return { node: input, offset: pos <= consumed ? i : i + 1 };
        }
        return _terminalComposeDescendantBoundary(child, pos - consumed) || { node: input, offset: i };
      }
      consumed += len;
      best = { node: input, offset: i + 1 };
    }
    return best;
  }
  const range = document.createRange();
  const first = boundaryFor(start);
  const last = boundaryFor(end);
  range.setStart(first.node, first.offset);
  range.setEnd(last.node, last.offset);
  const selection = window.getSelection();
  selection.removeAllRanges();
  if (direction === 'backward' && selection.extend) {
    const reverse = document.createRange();
    reverse.setStart(last.node, last.offset);
    reverse.collapse(true);
    selection.addRange(reverse);
    selection.extend(first.node, first.offset);
  } else {
    selection.addRange(range);
  }
  return true;
}

function _terminalComposeDescendantBoundary(node, offset) {
  const children = node && node.childNodes ? node.childNodes : [];
  let consumed = 0;
  for (let i = 0; i < children.length; i++) {
    const child = children[i];
    const len = _terminalComposeNodeTextLength(child);
    if (consumed + len >= offset) {
      if (child.nodeType === 3) return { node: child, offset: Math.max(0, Math.min(len, offset - consumed)) };
      if (String(child.nodeName || '').toUpperCase() === 'BR') return { node: node, offset: offset <= consumed ? i : i + 1 };
      return _terminalComposeDescendantBoundary(child, offset - consumed) || { node: node, offset: i };
    }
    consumed += len;
  }
  return { node: node, offset: children.length };
}

function _terminalComposeSyncValueFromDom(input) {
  if (!_terminalComposeIsRichInput(input)) return String(input && input.value || '');
  if (!input.childNodes) return String(input.value || '');
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  const stateForCell = cellId ? _terminalComposeAttachments[cellId] : null;
  const seen = {};
  let text = '';
  function appendText(value) {
    text += String(value || '').replace(/\u00a0/g, ' ');
  }
  function walk(node) {
    if (!node) return;
    if (node.nodeType === 3) {
      appendText(node.nodeValue || '');
      return;
    }
    if (node.nodeType !== 1) return;
    const token = node.getAttribute ? String(node.getAttribute('data-attachment-token') || '') : '';
    if (token) {
      seen[token] = true;
      const entry = _terminalComposeAttachmentEntry(cellId, token);
      if (entry) entry.position = text.length;
      return;
    }
    if (node.getAttribute && node.getAttribute('data-attachment-caret-host')) {
      // The first character is the structural ordinary space rendered after
      // an attachment. Text typed into this host belongs to the draft; strip
      // only that one boundary character so a later canonical rerender can
      // re-create exactly one space instead of accumulating them.
      let hostText = '';
      const hostChildren = node.childNodes || [];
      for (let i = 0; i < hostChildren.length; i++) {
        const child = hostChildren[i];
        if (child && child.nodeType === 3) hostText += String(child.nodeValue || '');
        else if (child && String(child.nodeName || '').toUpperCase() === 'BR') hostText += '\n';
        else if (child && typeof child.textContent === 'string') hostText += child.textContent;
      }
      hostText = hostText.replace(/\u00a0/g, ' ');
      if (hostText.charAt(0) === ' ') appendText(hostText.slice(1));
      else appendText(hostText);
      return;
    }
    const name = String(node.nodeName || '').toUpperCase();
    if (name === 'BR') {
      appendText('\n');
      return;
    }
    const isBlockLine = name === 'DIV' || name === 'P';
    if (isBlockLine && text.length && !text.endsWith('\n')) {
      appendText('\n');
    }
    const before = text.length;
    const children = node.childNodes || [];
    for (let i = 0; i < children.length; i++) walk(children[i]);
    if (isBlockLine && text.length > before && !text.endsWith('\n')) {
      appendText('\n');
    }
  }
  const children = input.childNodes || [];
  for (let i = 0; i < children.length; i++) walk(children[i]);
  if (text.endsWith('\n')) text = text.slice(0, -1);
  input.value = text;
  if (stateForCell && Array.isArray(stateForCell.entries)) {
    stateForCell.entries = stateForCell.entries.filter(function(entry) {
      if (!entry || seen[String(entry.token || '')]) return !!entry;
      _terminalComposeRevokePreviewUrl(_terminalComposeAttachmentPreviewUrl(entry));
      return false;
    });
    if (!stateForCell.entries.length) delete _terminalComposeAttachments[cellId];
  }
  return text;
}

function _terminalComposeRenderRichInput(input, options) {
  if (!_terminalComposeIsRichInput(input)) return;
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  const text = String(input.value || '');
  const preserve = !!(options && options.preserveSelection);
  const selection = preserve ? _terminalComposeSelectionOffsets(input) : null;
  const html = _terminalComposeRichHtml(cellId, text);
  if (input.innerHTML !== html) input.innerHTML = html;
  if (preserve && selection) {
    _terminalComposeSetRichSelection(input, selection.start, selection.end, selection.direction, options);
  }
}

function _terminalComposeSetInputText(input, text, options) {
  if (!input) return;
  input.value = String(text || '');
  if (_terminalComposeIsRichInput(input)) {
    _terminalComposeRenderRichInput(input, options || {});
  }
}

function _terminalComposeDecodeHtmlEntities(value) {
  return String(value || '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x([0-9a-f]+);/gi, function(_match, hex) {
      const code = parseInt(hex, 16);
      try {
        return Number.isFinite(code) ? String.fromCodePoint(code) : '';
      } catch (_e) {
        return '';
      }
    })
    .replace(/&#(\d+);/g, function(_match, num) {
      const code = parseInt(num, 10);
      try {
        return Number.isFinite(code) ? String.fromCodePoint(code) : '';
      } catch (_e) {
        return '';
      }
    });
}

function _terminalComposePlainTextFromHtml(html) {
  let text = String(html || '');
  if (!text) return '';
  text = text
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<(script|style)\b[\s\S]*?<\/\1>/gi, '')
    .replace(/<\s*br\s*\/?>/gi, '\n')
    .replace(/<\s*\/\s*(div|p|li|tr|h[1-6]|blockquote|pre)\s*>/gi, '\n')
    .replace(/<\s*(div|p|li|tr|h[1-6]|blockquote|pre)\b[^>]*>/gi, '\n')
    .replace(/<[^>]+>/g, '');
  return _terminalComposeDecodeHtmlEntities(text)
    .replace(/[ \t]*\n[ \t]*/g, '\n')
    .replace(/\n{2,}/g, '\n')
    .replace(/^\n+|\n+$/g, '');
}

function _terminalComposeNormalizePastedText(text) {
  return String(text || '')
    .replace(/\r\n?/g, '\n')
    .replace(/\u00a0/g, ' ');
}

function _terminalComposeClipboardPlainText(dataTransfer) {
  if (!dataTransfer || typeof dataTransfer.getData !== 'function') return '';
  let text = '';
  try {
    text = dataTransfer.getData('text/plain') || dataTransfer.getData('text') || '';
  } catch (_e) {
    text = '';
  }
  if (!text) {
    let html = '';
    try {
      html = dataTransfer.getData('text/html') || '';
    } catch (_e) {
      html = '';
    }
    if (html) text = _terminalComposePlainTextFromHtml(html);
  }
  return _terminalComposeNormalizePastedText(text);
}

function _terminalComposeInsertTextAtSelection(input, insertedText) {
  if (!input) return;
  const text = String(insertedText || '');
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  const oldText = _terminalComposeInputText(input);
  const selection = _terminalComposeSelectionOffsets(input);
  const start = Math.max(0, Math.min(oldText.length, selection.start));
  const end = Math.max(start, Math.min(oldText.length, selection.end));
  const nextText = oldText.slice(0, start) + text + oldText.slice(end);
  if (cellId) _terminalComposeAdjustAttachmentPositions(cellId, oldText, nextText);
  _terminalComposeSetInputText(input, nextText);
  const cursor = start + text.length;
  if (_terminalComposeIsRichInput(input)) {
    if (!_terminalComposeSetRichSelection(input, cursor, cursor, 'none', { afterAttachments: true })) {
      input.selectionStart = cursor;
      input.selectionEnd = cursor;
      if ('selectionDirection' in input) input.selectionDirection = 'none';
    }
  } else if (typeof input.setSelectionRange === 'function') {
    input.setSelectionRange(cursor, cursor);
  } else {
    input.selectionStart = cursor;
    input.selectionEnd = cursor;
    if ('selectionDirection' in input) input.selectionDirection = 'none';
  }
  terminalComposeInput(input);
}

function terminalComposePaste(evt, cellId) {
  const input = (evt && (evt.currentTarget || evt.target)) || (
    document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null
  );
  if (!input || !_terminalComposeIsRichInput(input)) return true;
  const text = _terminalComposeClipboardPlainText(evt && evt.clipboardData);
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
  _terminalComposeInsertTextAtSelection(input, text);
  if (typeof input.focus === 'function') input.focus();
  return false;
}

function _terminalComposeInputText(input) {
  if (!input) return '';
  if (_terminalComposeIsRichInput(input)) return _terminalComposeSyncValueFromDom(input);
  return String(input.value || '');
}

function _terminalComposeButtonFor(el, cellId) {
  const container = _terminalComposeContainerFor(el);
  if (container && container.querySelector) {
    const btn = container.querySelector('.terminal-compose-submit');
    if (btn) return btn;
  }
  const id = _terminalComposeButtonId(cellId || (el && el.dataset ? el.dataset.cellId : ''));
  return document.getElementById ? document.getElementById(id) : null;
}

function _terminalComposeAutoResizeInvalidate(cellId) {
  const id = String(cellId || '');
  if (id && _terminalComposeAutoResizeMemo[id]) delete _terminalComposeAutoResizeMemo[id];
}

function _terminalComposeAutoResize(el, opts) {
  if (!el) return;
  const cellId = el.dataset ? (el.dataset.cellId || '') : '';
  const force = !!(opts && opts.force);
  const text = _terminalComposeInputText(el);
  const storedHeight = _terminalComposeStoredHeight(cellId);
  // Skip the forced-reflow autoresize when nothing that determines the
  // composer's height has changed. We only skip while the input is focused:
  // a non-focused input always recomputes so a shrunk window/shell re-clamps
  // it on the next pass (shell/window geometry is otherwise driven by the
  // xterm ResizeObserver, not this memo), avoiding a stale over-tall composer.
  const focused = typeof document !== 'undefined' && document.activeElement === el;
  if (!force && cellId && focused) {
    const memo = _terminalComposeAutoResizeMemo[cellId];
    if (memo && memo.text === text && memo.storedHeight === storedHeight) return;
  }
  if (typeof taskAutoResize === 'function') {
    taskAutoResize(el);
  } else if (typeof boardAddTaskAutoResize === 'function') {
    boardAddTaskAutoResize(el);
  }
  _terminalComposeApplyHeight(el);
  if (cellId) _terminalComposeAutoResizeMemo[cellId] = { text: text, storedHeight: storedHeight };
}

function _terminalComposeHeightBounds(input) {
  const min = TERMINAL_COMPOSE_MIN_HEIGHT;
  let max = 0;
  const shell = input && typeof input.closest === 'function'
    ? input.closest('.terminal-shell')
    : null;
  if (shell && typeof shell.getBoundingClientRect === 'function') {
    const rect = shell.getBoundingClientRect();
    const shellHeight = rect && Number.isFinite(rect.height) ? rect.height : 0;
    if (shellHeight >= 260) max = Math.floor(shellHeight * 0.36);
  }
  if (!max && typeof window !== 'undefined' && typeof window.innerHeight === 'number') {
    max = Math.floor(window.innerHeight * 0.34);
  }
  max = Math.max(min, max || TERMINAL_COMPOSE_MAX_HEIGHT_FALLBACK);
  return { min: min, max: max };
}

function _terminalComposeClampHeight(input, height) {
  const raw = parseInt(height, 10);
  if (!Number.isFinite(raw) || raw <= 0) return 0;
  const bounds = _terminalComposeHeightBounds(input);
  return Math.max(bounds.min, Math.min(bounds.max, raw));
}

function _terminalComposeStoredHeight(cellId) {
  const id = String(cellId || '');
  const value = Number(
    _terminalComposeHeights[id]
    || (state ? state.terminal_compose_height : 0)
    || 0
  );
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

function _terminalComposeApplyHeight(input) {
  if (!input) return 0;
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  const saved = _terminalComposeStoredHeight(cellId);
  const clamped = saved ? _terminalComposeClampHeight(input, saved) : 0;
  const container = _terminalComposeContainerFor(input);
  if (clamped > 0) {
    input.style.maxHeight = clamped + 'px';
    input.style.height = clamped + 'px';
    if (input.style && typeof input.style.setProperty === 'function') {
      input.style.setProperty('--terminal-compose-user-height', clamped + 'px');
    } else {
      input.style['--terminal-compose-user-height'] = clamped + 'px';
    }
    if (container && container.dataset) container.dataset.composeResized = 'true';
  } else {
    input.style.maxHeight = '';
    if (input.style && typeof input.style.removeProperty === 'function') {
      input.style.removeProperty('--terminal-compose-user-height');
    } else if (input.style) {
      delete input.style['--terminal-compose-user-height'];
    }
    if (container && container.dataset) delete container.dataset.composeResized;
  }
  return clamped;
}

function _terminalComposeCurrentHeight(input) {
  if (!input) return TERMINAL_COMPOSE_DEFAULT_MAX_HEIGHT;
  if (typeof input.getBoundingClientRect === 'function') {
    const rect = input.getBoundingClientRect();
    if (rect && Number.isFinite(rect.height) && rect.height > 0) return rect.height;
  }
  if (Number(input.offsetHeight) > 0) return Number(input.offsetHeight);
  return _terminalComposeStoredHeight(input.dataset ? input.dataset.cellId : '')
    || TERMINAL_COMPOSE_DEFAULT_MAX_HEIGHT;
}

function _terminalComposeSetUserHeight(input, height) {
  if (!input) return 0;
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  const clamped = _terminalComposeClampHeight(input, height);
  if (cellId && clamped > 0) _terminalComposeHeights[cellId] = clamped;
  // Explicit resize interaction: drop the autoresize memo so the next
  // autoresize pass re-applies rather than skipping on an unchanged draft.
  _terminalComposeAutoResizeInvalidate(cellId);
  input.style.height = clamped + 'px';
  _terminalComposeApplyHeight(input);
  return clamped;
}

function _terminalComposePersistHeight(input, height) {
  const applied = _terminalComposeClampHeight(input || null, height);
  if (state) state.terminal_compose_height = applied;
  if (typeof send === 'function') {
    send({ cmd: 'ui_set_terminal_compose_height', height: applied });
  }
  return applied;
}

function _terminalComposeResizeInputFromEvent(event, cellId) {
  const target = event && (event.currentTarget || event.target);
  if (target && typeof target.closest === 'function') {
    const form = target.closest('.terminal-compose');
    if (form && typeof form.querySelector === 'function') {
      const input = form.querySelector('.terminal-compose-input');
      if (input) return input;
    }
  }
  const id = String(cellId || '');
  return document.getElementById ? document.getElementById(_terminalComposeInputId(id)) : null;
}

function terminalComposeResizeStart(event, cellId) {
  if (!event || (typeof event.button === 'number' && event.button !== 0)) return false;
  const input = _terminalComposeResizeInputFromEvent(event, cellId);
  if (!input) return false;
  if (typeof event.preventDefault === 'function') event.preventDefault();
  if (typeof event.stopPropagation === 'function') event.stopPropagation();
  _terminalComposeResizeDrag = {
    input: input,
    cellId: String(cellId || (input.dataset ? input.dataset.cellId : '') || ''),
    startY: Number.isFinite(event.clientY) ? event.clientY : 0,
    startHeight: _terminalComposeCurrentHeight(input),
    currentHeight: _terminalComposeCurrentHeight(input),
    changed: false,
  };
  if (document && document.body) {
    if (document.body.classList) document.body.classList.add('terminal-compose-resizing');
    if (document.body.style) document.body.style.cursor = 'ns-resize';
  }
  document.addEventListener('mousemove', _terminalComposeResizeMove);
  document.addEventListener('mouseup', _terminalComposeResizeEnd);
  return false;
}

function _terminalComposeResizeMove(event) {
  const drag = _terminalComposeResizeDrag;
  if (!drag) return;
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  const clientY = event && Number.isFinite(event.clientY) ? event.clientY : drag.startY;
  const next = drag.startHeight - (clientY - drag.startY);
  drag.currentHeight = _terminalComposeSetUserHeight(drag.input, next);
  drag.changed = true;
}

function _terminalComposeClearResizeDrag() {
  document.removeEventListener('mousemove', _terminalComposeResizeMove);
  document.removeEventListener('mouseup', _terminalComposeResizeEnd);
  if (document && document.body) {
    if (document.body.classList) document.body.classList.remove('terminal-compose-resizing');
    if (document.body.style) document.body.style.cursor = '';
  }
  _terminalComposeResizeDrag = null;
}

function _terminalComposeResizeEnd(event) {
  const drag = _terminalComposeResizeDrag;
  if (!drag) return;
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  if (event && Number.isFinite(event.clientY)) {
    const next = drag.startHeight - (event.clientY - drag.startY);
    drag.currentHeight = _terminalComposeSetUserHeight(drag.input, next);
    if (event.clientY !== drag.startY) drag.changed = true;
  }
  const shouldPersist = !!drag.changed;
  const height = drag.currentHeight;
  _terminalComposeClearResizeDrag();
  if (shouldPersist) _terminalComposePersistHeight(drag.input, height);
}

function terminalComposeResizeKeydown(event, cellId) {
  const key = event && (event.key || event.code);
  const deltas = {
    ArrowUp: 14,
    Up: 14,
    ArrowDown: -14,
    Down: -14,
    PageUp: 56,
    PageDown: -56,
  };
  if (!Object.prototype.hasOwnProperty.call(deltas, key)) return false;
  const input = _terminalComposeResizeInputFromEvent(event, cellId);
  if (!input) return false;
  if (typeof event.preventDefault === 'function') event.preventDefault();
  if (typeof event.stopPropagation === 'function') event.stopPropagation();
  const applied = _terminalComposeSetUserHeight(input, _terminalComposeCurrentHeight(input) + deltas[key]);
  _terminalComposePersistHeight(input, applied);
  if (typeof input.focus === 'function') input.focus();
  return false;
}

function _terminalComposeSetButtonState(input) {
  if (!input) return;
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  const button = _terminalComposeButtonFor(input, cellId);
  const attachments = _terminalComposeSortedAttachments(cellId);
  if (button) button.disabled = !_terminalComposeInputText(input).trim() && !attachments.length;
}

function _terminalMessageHistoryEntries(cellId) {
  const id = String(cellId || '');
  const history = state && state.agent_message_history
    ? state.agent_message_history[id]
    : null;
  if (!Array.isArray(history)) return [];
  return history.filter(function(entry) {
    return entry && typeof entry.message === 'string' && entry.message.length;
  });
}

function _terminalComposeHistoryButtonFor(cellId) {
  return document.getElementById
    ? document.getElementById(_terminalComposeHistoryButtonId(cellId))
    : null;
}

function _terminalComposeHistoryMenuFor(cellId) {
  return document.getElementById
    ? document.getElementById(_terminalComposeHistoryMenuId(cellId))
    : null;
}

function _terminalComposeHistoryPreview(message) {
  const text = String(message || '').replace(/\s+/g, ' ').trim();
  if (text.length <= 160) return text || '(empty message)';
  return text.slice(0, 157) + '\u2026';
}

function _terminalComposeHistoryRenderMenu(cellId) {
  const id = String(cellId || '');
  const menu = _terminalComposeHistoryMenuFor(id);
  if (!menu) return;
  const entries = _terminalMessageHistoryEntries(id).slice(0, 12);
  let html = ''
    + '<div class="terminal-compose-history-title ui-menu-label">Recent messages</div>';
  if (!entries.length) {
    html += '<div class="terminal-compose-history-empty">'
      + 'No sent messages yet.'
      + '</div>';
  } else {
    html += '<div class="terminal-compose-history-list">';
    for (let i = 0; i < entries.length; i++) {
      const preview = _terminalComposeHistoryPreview(entries[i].message);
      html += '<button type="button" class="terminal-compose-history-item ui-menu-item"'
        + ' role="option" data-cell-id="' + esc(id) + '" data-history-index="' + i + '"'
        + ' onclick="return terminalComposeHistoryPick(event)"'
        + ' title="' + esc(preview) + '">'
        + esc(preview)
        + '</button>';
    }
    html += '</div>';
  }
  html += '<div class="terminal-compose-history-hint">\u2191/\u2193 also recall history</div>';
  menu.innerHTML = html;
}

function _terminalComposeHistoryIsOpen(cellId) {
  const id = String(cellId || '');
  const menu = _terminalComposeHistoryMenuFor(id);
  return !!(id && _terminalComposeHistoryOpenCellId === id && menu && menu.hidden !== true);
}

function _terminalComposeHistoryClose(cellId, focusButton) {
  const id = String(cellId || _terminalComposeHistoryOpenCellId || '');
  if (!id) return;
  const menu = _terminalComposeHistoryMenuFor(id);
  if (menu) menu.hidden = true;
  const button = _terminalComposeHistoryButtonFor(id);
  if (button && typeof button.setAttribute === 'function') {
    button.setAttribute('aria-expanded', 'false');
  }
  if (_terminalComposeHistoryOpenCellId === id) _terminalComposeHistoryOpenCellId = '';
  if (focusButton && button && typeof button.focus === 'function') button.focus();
}

function _terminalComposeHistoryOpen(cellId) {
  const id = String(cellId || '');
  if (!id) return;
  if (_terminalComposeHistoryOpenCellId && _terminalComposeHistoryOpenCellId !== id) {
    _terminalComposeHistoryClose(_terminalComposeHistoryOpenCellId);
  }
  _terminalComposeHistoryRenderMenu(id);
  const menu = _terminalComposeHistoryMenuFor(id);
  if (!menu) return;
  menu.hidden = false;
  const button = _terminalComposeHistoryButtonFor(id);
  if (button && typeof button.setAttribute === 'function') {
    button.setAttribute('aria-expanded', 'true');
  }
  _terminalComposeHistoryOpenCellId = id;
  const firstItem = menu.querySelector && menu.querySelector('.terminal-compose-history-item');
  if (firstItem && typeof firstItem.focus === 'function') firstItem.focus();
}

function _terminalComposeHistoryHandleDocumentClick(evt) {
  const id = _terminalComposeHistoryOpenCellId;
  if (!id) return;
  const target = evt ? evt.target : null;
  const menu = _terminalComposeHistoryMenuFor(id);
  const button = _terminalComposeHistoryButtonFor(id);
  if (target && (
      (menu && typeof menu.contains === 'function' && menu.contains(target))
      || (button && (button === target
        || (typeof button.contains === 'function' && button.contains(target)))))) {
    return;
  }
  _terminalComposeHistoryClose(id);
}

function _terminalComposeHistoryHandleDocumentKeydown(evt) {
  if (!_terminalComposeHistoryOpenCellId || !evt) return;
  const menu = _terminalComposeHistoryMenuFor(_terminalComposeHistoryOpenCellId);
  const items = menu && menu.querySelectorAll
    ? Array.prototype.slice.call(menu.querySelectorAll('.terminal-compose-history-item'))
    : [];
  if (evt.key === 'Escape') {
    _terminalComposeHistoryClose(_terminalComposeHistoryOpenCellId, true);
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    return;
  }
  if (!items.length || ['ArrowDown', 'ArrowUp', 'Home', 'End'].indexOf(evt.key) < 0) return;
  let index = items.indexOf(document.activeElement);
  if (evt.key === 'Home') index = 0;
  else if (evt.key === 'End') index = items.length - 1;
  else if (evt.key === 'ArrowDown') index = index < 0 ? 0 : (index + 1) % items.length;
  else index = index < 0 ? items.length - 1 : (index - 1 + items.length) % items.length;
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  items[index].focus();
}

if (typeof document !== 'undefined' && document.addEventListener) {
  document.addEventListener('click', _terminalComposeHistoryHandleDocumentClick);
  document.addEventListener('keydown', _terminalComposeHistoryHandleDocumentKeydown, true);
}

function terminalComposeHistoryToggle(evt, cellId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  const id = String(cellId || '');
  if (!id) return false;
  if (_terminalComposeHistoryIsOpen(id)) {
    _terminalComposeHistoryClose(id);
  } else {
    _terminalComposeHistoryOpen(id);
  }
  return false;
}

function terminalComposeHistoryPick(evt, cellId, index) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  const target = evt && evt.currentTarget ? evt.currentTarget : null;
  const id = String(cellId || (target && target.dataset ? target.dataset.cellId : '') || '');
  const entries = _terminalMessageHistoryEntries(id);
  const rawIndex = index != null
    ? index
    : (target && target.dataset ? target.dataset.historyIndex : 0);
  const idx = Math.max(0, Math.min(entries.length - 1, Number(rawIndex) || 0));
  const entry = entries[idx];
  if (!id || !entry) {
    _terminalComposeHistoryClose(id);
    return false;
  }
  const input = document.getElementById
    ? document.getElementById(_terminalComposeInputId(id))
    : null;
  if (input) {
    const recall = _terminalComposeRecallState(id);
    recall.draft = _terminalComposeInputText(input);
    recall.index = idx;
    _terminalComposeSetValue(input, id, entry.message, { preserveAttachments: true });
    if (typeof input.focus === 'function') input.focus();
  }
  _terminalComposeHistoryClose(id);
  return false;
}

function _terminalComposeSlashDropdownFor(cellId) {
  return document.getElementById
    ? document.getElementById(_terminalComposeSlashDropdownId(cellId))
    : null;
}

function _terminalComposeSlashEnabled(input) {
  if (!input) return false;
  var cellId = input.dataset ? String(input.dataset.cellId || '') : '';
  if (input.dataset && String(input.dataset.agentId || '').trim()) return true;
  return !!_terminalComposeDirectAgentForCellId(cellId);
}

function _terminalComposeSlashTrigger(input) {
  if (!_terminalComposeSlashEnabled(input)) return null;
  var value = _terminalComposeInputText(input);
  var selection = _terminalComposeSelectionOffsets(input);
  var start = selection.start;
  var end = selection.end;
  if (start !== end) return null;
  start = Math.max(0, Math.min(value.length, start));
  var before = value.slice(0, start);
  var lineStart = before.lastIndexOf('\n') + 1;
  if (lineStart !== 0) return null;
  var prefix = before.slice(lineStart);
  if (!prefix || prefix.charAt(0) !== '/') return null;
  return {
    start: 0,
    end: value.length,
    query: prefix.slice(1).toLowerCase(),
  };
}

function _terminalComposeSlashCandidates(query) {
  var needle = String(query || '').trim().toLowerCase().replace(/\s+/g, ' ');
  var results = [];
  // The backend catalog is the only command contract.  Missing/invalid
  // catalog data deliberately yields no dropdown, while normal DM sending
  // continues unchanged for offline or older snapshots.
  var catalog = state && Array.isArray(state.user_dm_commands)
    ? state.user_dm_commands
    : [];
  for (var i = 0; i < catalog.length; i++) {
    var item = catalog[i] || {};
    var search = String((item.search || '') + ' ' + (item.label || '') + ' ' + (item.usage || ''))
      .toLowerCase()
      .replace(/^\//, '')
      .replace(/\s+/g, ' ');
    if (!needle || search.indexOf(needle) >= 0) results.push(item);
  }
  return results;
}

function _terminalComposeSlashDropdownHide(cellId) {
  var id = String(cellId || _terminalComposeSlashDropdownCellId || '');
  var dropdown = id ? _terminalComposeSlashDropdownFor(id) : null;
  if (dropdown) dropdown.style.display = 'none';
  if (!id || _terminalComposeSlashDropdownCellId === id) {
    _terminalComposeSlashDropdownCellId = '';
    _terminalComposeSlashDropdownIdx = -1;
    _terminalComposeSlashDropdownResults = [];
  }
}

function _terminalComposeSlashDropdownVisible(cellId) {
  var id = String(cellId || '');
  var dropdown = id ? _terminalComposeSlashDropdownFor(id) : null;
  return !!(id
    && _terminalComposeSlashDropdownCellId === id
    && dropdown
    && dropdown.style.display !== 'none');
}

function _terminalComposeHighlightSlashOpt(opts) {
  for (var i = 0; i < opts.length; i++) {
    var selected = i === _terminalComposeSlashDropdownIdx;
    opts[i].classList.toggle('active', selected);
    opts[i].setAttribute('aria-selected', selected ? 'true' : 'false');
  }
}

function _terminalComposeUpdateSlashDropdown(input) {
  if (!input) return false;
  var cellId = input.dataset ? (input.dataset.cellId || '') : '';
  var dropdown = cellId ? _terminalComposeSlashDropdownFor(cellId) : null;
  if (!dropdown) {
    _terminalComposeSlashDropdownHide(cellId);
    return false;
  }
  var trigger = _terminalComposeSlashTrigger(input);
  if (!trigger) {
    _terminalComposeSlashDropdownHide(cellId);
    return false;
  }
  var results = _terminalComposeSlashCandidates(trigger.query);
  if (!results.length) {
    _terminalComposeSlashDropdownHide(cellId);
    return false;
  }
  _terminalComposeSlashDropdownCellId = cellId;
  _terminalComposeSlashDropdownIdx = -1;
  _terminalComposeSlashDropdownResults = results;
  var html = '';
  for (var i = 0; i < results.length; i++) {
    var item = results[i] || {};
    html += '<div class="deps-option terminal-compose-slash-option ui-menu-item"'
      + ' role="option" aria-selected="false" data-slash-command="' + esc(item.id || '') + '"'
      + ' onmousedown="return terminalComposePickSlashCommand(event, \''
      + esc(cellId).replace(/'/g, "\\'") + '\', ' + i + ')">'
      + '<div class="terminal-compose-slash-main">'
      + '<span class="terminal-compose-slash-label">' + esc(item.label || '') + '</span>'
      + (item.usage && item.usage !== item.label
        ? '<span class="terminal-compose-slash-usage">' + esc(item.usage) + '</span>'
        : '')
      + '</div>'
      + (item.help ? '<div class="terminal-compose-slash-help">' + esc(item.help) + '</div>' : '')
      + '</div>';
  }
  dropdown.innerHTML = html;
  dropdown.style.display = '';
  return true;
}

function _terminalComposeUpdateAutocomplete(input) {
  if (!input) return;
  var cellId = input.dataset ? (input.dataset.cellId || '') : '';
  if (_terminalComposeUpdateSlashDropdown(input)) {
    _terminalComposeTaskDropdownHide(cellId);
    return;
  }
  _terminalComposeUpdateTaskDropdown(input);
}

function _terminalComposeSlashDropdownHandleKey(evt, cellId) {
  var id = String(cellId || '');
  if (!_terminalComposeSlashDropdownVisible(id)) return false;
  var count = _terminalComposeSlashDropdownResults.length;
  if (!count) {
    _terminalComposeSlashDropdownHide(id);
    return false;
  }
  var dropdown = _terminalComposeSlashDropdownFor(id);
  var opts = dropdown && dropdown.querySelectorAll
    ? dropdown.querySelectorAll('.deps-option')
    : [];
  if (evt.key === 'ArrowDown') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    _terminalComposeSlashDropdownIdx = (_terminalComposeSlashDropdownIdx + 1) % count;
    _terminalComposeHighlightSlashOpt(opts);
    return true;
  }
  if (evt.key === 'ArrowUp') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    _terminalComposeSlashDropdownIdx = _terminalComposeSlashDropdownIdx <= 0
      ? count - 1
      : _terminalComposeSlashDropdownIdx - 1;
    _terminalComposeHighlightSlashOpt(opts);
    return true;
  }
  if (evt.key === 'Enter' || evt.key === 'Tab') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    var idx = _terminalComposeSlashDropdownIdx >= 0 ? _terminalComposeSlashDropdownIdx : 0;
    terminalComposePickSlashCommand(evt, id, idx);
    return true;
  }
  if (evt.key === 'Escape') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    _terminalComposeSlashDropdownHide(id);
    return true;
  }
  return false;
}

function terminalComposePickSlashCommand(evt, cellId, index) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  var id = String(cellId || '');
  var input = document.getElementById
    ? document.getElementById(_terminalComposeInputId(id))
    : null;
  if (!input) return false;
  var idx = Math.max(0, Math.min(_terminalComposeSlashDropdownResults.length - 1, Number(index) || 0));
  var item = _terminalComposeSlashDropdownResults[idx] || null;
  var insertText = item ? String(item.insert || item.label || '').trimStart() : '';
  if (!insertText) {
    _terminalComposeSlashDropdownHide(id);
    return false;
  }
  _terminalComposeSetInputText(input, insertText);
  var cursor = insertText.length;
  if (_terminalComposeIsRichInput(input)) {
    _terminalComposeSetRichSelection(input, cursor, cursor, 'none', { afterAttachments: true });
  } else if (typeof input.setSelectionRange === 'function') {
    input.setSelectionRange(cursor, cursor);
  } else {
    input.selectionStart = cursor;
    input.selectionEnd = cursor;
    if ('selectionDirection' in input) input.selectionDirection = 'none';
  }
  if (id) {
    _terminalComposePruneAttachments(id, insertText);
    _terminalComposeDrafts[id] = insertText;
    if (!_terminalComposeIsRichInput(input)) _terminalComposeRefreshAttachmentChips(id);
    if (_terminalComposeErrors[id]) _terminalComposeSetError(input, '');
  }
  _terminalComposeAutoResize(input);
  _terminalComposeSetButtonState(input);
  _terminalComposeSlashDropdownHide(id);
  if (typeof input.focus === 'function') input.focus();
  return false;
}

function _terminalComposeTaskDropdownFor(cellId) {
  return document.getElementById
    ? document.getElementById(_terminalComposeTaskDropdownId(cellId))
    : null;
}

function _terminalComposeTaskTrigger(input) {
  if (!input) return null;
  var value = _terminalComposeInputText(input);
  var selection = _terminalComposeSelectionOffsets(input);
  var start = selection.start;
  var end = selection.end;
  if (start !== end) return null;
  start = Math.max(0, Math.min(value.length, start));
  var before = value.slice(0, start);
  var match = before.match(/(^|\s):([\w:-]*)$/);
  if (!match) return null;
  return {
    start: before.length - match[2].length - 1,
    end: start,
    query: String(match[2] || '').toLowerCase(),
  };
}

function _terminalComposeTaskTitle(task) {
  return String((task && (task.task || task.title)) || '').trim();
}

function _terminalComposeTaskGroup(cellId) {
  const id = String(cellId || '').trim();
  const cell = id && state && state.agents ? state.agents[id] : null;
  if (cell && cell.group) return String(cell.group || '');
  const agent = _terminalComposeDirectAgentForCellId(id);
  if (agent && agent.group) return String(agent.group || '');
  return _terminalCurrentGroupName();
}

function _terminalComposeTaskCandidates(query, cellId) {
  var needle = String(query || '').trim().toLowerCase();
  var tasks = state && state.board_tasks ? state.board_tasks : {};
  var group = _terminalComposeTaskGroup(cellId);
  var matches = [];
  if (!group) return matches;
  for (var id in tasks) {
    var task = tasks[id];
    if (!task || String(task.archived_at || '').trim()
        || String(task.lane || '') === 'Archived') {
      continue;
    }
    if (String(task.group || '') !== group) continue;
    var taskId = String(task.id || id || '').trim();
    if (!taskId) continue;
    var title = _terminalComposeTaskTitle(task);
    var search = (taskId + ' ' + title).toLowerCase();
    if (!needle || search.indexOf(needle) >= 0) {
      matches.push({ id: taskId, title: title });
      if (matches.length >= 8) break;
    }
  }
  return matches;
}

function _terminalComposeTaskDropdownHide(cellId) {
  var id = String(cellId || _terminalComposeTaskDropdownCellId || '');
  var dropdown = id ? _terminalComposeTaskDropdownFor(id) : null;
  if (dropdown) dropdown.style.display = 'none';
  if (!id || _terminalComposeTaskDropdownCellId === id) {
    _terminalComposeTaskDropdownCellId = '';
    _terminalComposeTaskDropdownIdx = -1;
    _terminalComposeTaskDropdownResults = [];
  }
}

function _terminalComposeTaskDropdownVisible(cellId) {
  var id = String(cellId || '');
  var dropdown = id ? _terminalComposeTaskDropdownFor(id) : null;
  return !!(id
    && _terminalComposeTaskDropdownCellId === id
    && dropdown
    && dropdown.style.display !== 'none');
}

function _terminalComposeHighlightTaskOpt(opts) {
  for (var i = 0; i < opts.length; i++) {
    var selected = i === _terminalComposeTaskDropdownIdx;
    opts[i].classList.toggle('active', selected);
    opts[i].setAttribute('aria-selected', selected ? 'true' : 'false');
  }
}

function _terminalComposeUpdateTaskDropdown(input) {
  if (!input) return;
  var cellId = input.dataset ? (input.dataset.cellId || '') : '';
  var dropdown = cellId ? _terminalComposeTaskDropdownFor(cellId) : null;
  if (!dropdown) return;
  var trigger = _terminalComposeTaskTrigger(input);
  if (!trigger) {
    _terminalComposeTaskDropdownHide(cellId);
    return;
  }
  var results = _terminalComposeTaskCandidates(trigger.query, cellId);
  if (!results.length) {
    _terminalComposeTaskDropdownHide(cellId);
    return;
  }
  _terminalComposeTaskDropdownCellId = cellId;
  _terminalComposeTaskDropdownIdx = -1;
  _terminalComposeTaskDropdownResults = results;
  var html = '';
  for (var i = 0; i < results.length; i++) {
    var taskId = String(results[i].id || '');
    var title = String(results[i].title || '');
    var jsTaskId = esc(taskId).replace(/'/g, "\\'");
    html += '<div class="deps-option terminal-compose-task-option ui-menu-item"'
      + ' role="option" aria-selected="false" data-task-id="' + esc(taskId) + '"'
      + ' onmousedown="return terminalComposePickTaskRef(event, \''
      + esc(cellId).replace(/'/g, "\\'") + '\', \'' + jsTaskId + '\')">'
      + '<span class="terminal-compose-task-ref-id">' + esc(taskId) + '</span>'
      + (title
        ? '<span class="terminal-compose-task-ref-title">' + esc(title) + '</span>'
        : '')
      + '</div>';
  }
  dropdown.innerHTML = html;
  dropdown.style.display = '';
}

function _terminalComposeTaskDropdownHandleKey(evt, cellId) {
  var id = String(cellId || '');
  if (!_terminalComposeTaskDropdownVisible(id)) return false;
  var count = _terminalComposeTaskDropdownResults.length;
  if (!count) {
    _terminalComposeTaskDropdownHide(id);
    return false;
  }
  var dropdown = _terminalComposeTaskDropdownFor(id);
  var opts = dropdown && dropdown.querySelectorAll
    ? dropdown.querySelectorAll('.deps-option')
    : [];
  if (evt.key === 'ArrowDown') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    _terminalComposeTaskDropdownIdx = (_terminalComposeTaskDropdownIdx + 1) % count;
    _terminalComposeHighlightTaskOpt(opts);
    return true;
  }
  if (evt.key === 'ArrowUp') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    _terminalComposeTaskDropdownIdx = _terminalComposeTaskDropdownIdx <= 0
      ? count - 1
      : _terminalComposeTaskDropdownIdx - 1;
    _terminalComposeHighlightTaskOpt(opts);
    return true;
  }
  if (evt.key === 'Enter') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    var idx = _terminalComposeTaskDropdownIdx >= 0 ? _terminalComposeTaskDropdownIdx : 0;
    var result = _terminalComposeTaskDropdownResults[idx];
    if (result) terminalComposePickTaskRef(evt, id, result.id);
    return true;
  }
  if (evt.key === 'Escape') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    _terminalComposeTaskDropdownHide(id);
    return true;
  }
  return false;
}

function terminalComposePickTaskRef(evt, cellId, taskId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  var id = String(cellId || '');
  var input = document.getElementById
    ? document.getElementById(_terminalComposeInputId(id))
    : null;
  if (!input) return false;
  var value = _terminalComposeInputText(input);
  var selection = _terminalComposeSelectionOffsets(input);
  var trigger = _terminalComposeTaskTrigger(input);
  var start = trigger ? trigger.start : selection.start;
  var end = trigger ? trigger.end : selection.end;
  start = Math.max(0, Math.min(value.length, start));
  end = Math.max(start, Math.min(value.length, end));
  var insertText = String(taskId || '').trim();
  if (!insertText) {
    _terminalComposeTaskDropdownHide(id);
    return false;
  }
  insertText += ' ';
  _terminalComposeSetInputText(input, value.slice(0, start) + insertText + value.slice(end));
  var cursor = start + insertText.length;
  if (_terminalComposeIsRichInput(input)) {
    _terminalComposeSetRichSelection(input, cursor, cursor, 'none', { afterAttachments: true });
  } else if (typeof input.setSelectionRange === 'function') {
    input.setSelectionRange(cursor, cursor);
  } else {
    input.selectionStart = cursor;
    input.selectionEnd = cursor;
    if ('selectionDirection' in input) input.selectionDirection = 'none';
  }
  _terminalComposeTaskDropdownHide(id);
  terminalComposeInput(input);
  if (typeof input.focus === 'function') input.focus();
  return false;
}

function _terminalComposeRecallState(cellId) {
  const id = String(cellId || '');
  if (!_terminalComposeRecall[id]) {
    _terminalComposeRecall[id] = { index: -1, draft: '' };
  }
  return _terminalComposeRecall[id];
}

function _terminalComposeResetRecall(cellId) {
  const id = String(cellId || '');
  if (id) delete _terminalComposeRecall[id];
}

function _terminalComposeSetValue(input, cellId, value, options) {
  if (!input) return;
  const id = String(cellId || (input.dataset ? input.dataset.cellId : '') || '');
  _terminalComposeSetInputText(input, String(value || ''));
  const preserveAttachments = !!(options && options.preserveAttachments);
  const text = _terminalComposeInputText(input);
  if (id && !preserveAttachments) _terminalComposePruneAttachments(id, text);
  if (id && preserveAttachments) _terminalComposePruneAttachments(id, text);
  if (id) _terminalComposeTaskDropdownHide(id);
  if (id) _terminalComposeDrafts[id] = text;
  const end = text.length;
  if (_terminalComposeIsRichInput(input)) {
    _terminalComposeSetRichSelection(input, end, end, 'none', { afterAttachments: true });
  } else if (typeof input.setSelectionRange === 'function') {
    input.setSelectionRange(end, end);
  } else {
    input.selectionStart = end;
    input.selectionEnd = end;
    if ('selectionDirection' in input) input.selectionDirection = 'none';
  }
  _terminalComposeAutoResize(input);
  _terminalComposeSetButtonState(input);
  if (id) _terminalComposeRefreshAttachmentChips(id);
}

function _terminalComposeCaretAtFirstLine(input) {
  if (!input) return true;
  const value = _terminalComposeInputText(input);
  const caret = _terminalComposeActiveSelection(input);
  return value.lastIndexOf('\n', Math.max(0, caret - 1)) < 0;
}

function _terminalComposeCaretAtLastLine(input) {
  if (!input) return true;
  const value = _terminalComposeInputText(input);
  const caret = _terminalComposeActiveSelection(input);
  return value.indexOf('\n', caret) < 0;
}

function _terminalComposeHistoryNavigate(input, cellId, direction) {
  const id = String(cellId || '');
  const entries = _terminalMessageHistoryEntries(id);
  if (!input || !id || !entries.length) return false;
  const recall = _terminalComposeRecallState(id);
  if (recall.index < 0) {
    if (direction > 0) return false;
    recall.draft = _terminalComposeInputText(input);
    recall.index = 0;
  } else if (direction < 0) {
    recall.index = Math.min(entries.length - 1, recall.index + 1);
  } else {
    recall.index -= 1;
  }

  if (recall.index < 0) {
    const draft = recall.draft || '';
    _terminalComposeResetRecall(id);
    _terminalComposeSetValue(input, id, draft, { preserveAttachments: true });
    return true;
  }

  _terminalComposeSetValue(input, id, entries[recall.index].message, { preserveAttachments: true });
  return true;
}

function _terminalComposeRestoreRecallDraft(input, cellId) {
  const id = String(cellId || '');
  const recall = _terminalComposeRecall[id];
  if (!recall || recall.index < 0) return false;
  const draft = recall.draft || '';
  _terminalComposeResetRecall(id);
  _terminalComposeSetValue(input, id, draft, { preserveAttachments: true });
  return true;
}

function _terminalComposeErrorElement(input) {
  const container = _terminalComposeContainerFor(input);
  return container && container.querySelector
    ? container.querySelector('.terminal-compose-error')
    : null;
}

function _terminalComposeSetError(input, message) {
  if (!input) return;
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  const text = String(message || '');
  if (cellId) _terminalComposeErrors[cellId] = text;
  const el = _terminalComposeErrorElement(input);
  if (el) el.textContent = text;
}

function _terminalComposeSetDropTarget(input, active) {
  if (!input || !input.classList) return;
  input.classList.toggle('terminal-compose-drop-target', !!active);
  const container = _terminalComposeContainerFor(input);
  if (container && container.classList) {
    container.classList.toggle('terminal-compose-drop-target', !!active);
  }
}

function _terminalComposePersistFromDom(root) {
  const input = _terminalComposeTextarea(root);
  if (!input || !input.dataset || !input.dataset.cellId) return;
  _terminalComposeDrafts[input.dataset.cellId] = _terminalComposeInputText(input);
}

function _captureTerminalWorkspaceState(root, cell) {
  if (typeof _captureSurfaceState !== 'function') return null;
  const snapshot = _captureSurfaceState(root);
  const active = document.activeElement;
  if (snapshot && snapshot.focus && active && active.dataset
      && active.dataset.cellId
      && cell && active.dataset.cellId !== String(cell.id || '')) {
    snapshot.focus = null;
  }
  if (snapshot) snapshot.terminalDirectMessages = _captureTerminalDirectMessagesState(root);
  return snapshot;
}

function _terminalWorkspaceFocusedComposeHasDraft(root) {
  if (!root || typeof document === 'undefined') return false;
  const active = document.activeElement;
  if (!active) return false;
  if (typeof root.contains === 'function' && !root.contains(active)) return false;
  if (!(active.classList && active.classList.contains('terminal-compose-input'))) return false;
  return _terminalComposeInputText(active).length > 0;
}

function _restoreTerminalWorkspaceState(root, snapshot, cell) {
  if (typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(root, snapshot);
  }
  _restoreTerminalDirectMessagesState(root, snapshot);
  const input = _terminalComposeTextarea(root);
  if (!input) return;
  const cellId = input.dataset ? (input.dataset.cellId || '') : '';
  // Only re-assign value if the rendered textarea actually drifted from the
  // in-memory draft. Re-assigning a textarea's value resets its scrollTop
  // and would undo the cursor-into-view scroll that _restoreSurfaceState
  // just performed.
  if (cell && cellId === String(cell.id || '')
      && Object.prototype.hasOwnProperty.call(_terminalComposeDrafts, cellId)
      && _terminalComposeInputText(input) !== _terminalComposeDrafts[cellId]) {
    _terminalComposeSetInputText(input, _terminalComposeDrafts[cellId], { preserveSelection: true });
  }
  if (!_terminalComposeOwnsLiveEditing(input)) {
    _terminalComposeRenderRichInput(input, { preserveSelection: true });
  }
  _terminalComposeAutoResize(input);
  _terminalComposeSetButtonState(input);
}

function _renderTerminalCompose(root, cell) {
  if (!root) return;
  const directAgent = _terminalDirectMessageAgent(cell);
  if (!cell || (!cell.session_id && !directAgent)) {
    if (root.innerHTML !== '') root.innerHTML = '';
    return;
  }
  const cellId = String(cell.id || '');
  const directAgentId = directAgent ? String(directAgent.id || '') : '';
  const inputId = _terminalComposeInputId(cellId);
  const buttonId = _terminalComposeButtonId(cellId);
  const historyButtonId = _terminalComposeHistoryButtonId(cellId);
  const historyMenuId = _terminalComposeHistoryMenuId(cellId);
  const taskDropdownId = _terminalComposeTaskDropdownId(cellId);
  const slashDropdownId = _terminalComposeSlashDropdownId(cellId);
  const draft = Object.prototype.hasOwnProperty.call(_terminalComposeDrafts, cellId)
    ? _terminalComposeDrafts[cellId]
    : '';
  const error = Object.prototype.hasOwnProperty.call(_terminalComposeErrors, cellId)
    ? _terminalComposeErrors[cellId]
    : '';
  const disabled = !String(draft || '').trim() && !_terminalComposeSortedAttachments(cellId).length;
  const placeholder = 'Send a message to ' + ((directAgent && directAgent.name) || cell.name || 'terminal') + '\u2026';
  const replyToId = directAgentId
    ? String(_terminalDirectMessageReplyToByAgent[directAgentId] || '')
    : '';
  const replyRow = replyToId ? _terminalDirectMessageById(directAgentId, replyToId) : null;
  const replyHtml = replyToId
    ? '<div class="terminal-compose-reply" data-reply-to-id="' + esc(replyToId) + '">'
      + '<span>Replying to ' + esc(replyRow ? _terminalDirectMessagePreview(replyRow) : replyToId) + '</span>'
      + '<button type="button" class="terminal-compose-reply-cancel"'
      + ' onclick="return terminalDirectMessageCancelReply(event, \'' + esc(directAgentId) + '\')"'
      + ' aria-label="Cancel reply">×</button>'
      + '</div>'
    : '';

  // Idempotent path: if the form already exists for this cell, update only
  // the dynamic bits (placeholder, error, button disabled, draft value if it
  // drifted) without clobbering the editor \u2014 clobbering destroys focus and
  // produces the TORQUE:264 textbox-border flicker under multi-agent activity.
  const existingForm = root.querySelector ? root.querySelector('.terminal-compose') : null;
  const existingCellId = existingForm && existingForm.dataset
    ? String(existingForm.dataset.cellId || '')
    : '';
  const existingReplyToId = existingForm && existingForm.dataset
    ? String(existingForm.dataset.replyToId || '')
    : '';
  const existingDirectAgentId = existingForm && existingForm.dataset
    ? String(existingForm.dataset.agentId || '')
    : '';
  if (existingForm
      && existingCellId === cellId
      && existingReplyToId === replyToId
      && existingDirectAgentId === directAgentId) {
    const input = _terminalComposeTextarea(root);
    if (input) {
      if (input.placeholder !== placeholder) input.placeholder = placeholder;
      if (_terminalComposeIsRichInput(input)
          && typeof input.setAttribute === 'function'
          && input.getAttribute('data-placeholder') !== placeholder) {
        input.setAttribute('data-placeholder', placeholder);
      }
      if (_terminalComposeInputText(input) !== draft) {
        _terminalComposeSetInputText(input, draft, { preserveSelection: true });
      }
      _terminalComposeAutoResize(input);
      _terminalComposeSetButtonState(input);
    }
    _terminalComposeRenderAttachmentChips(existingForm, cellId, { preserveActiveRichEditor: true });
    const errorEl = existingForm.querySelector
      ? existingForm.querySelector('.terminal-compose-error')
      : null;
    if (errorEl && errorEl.textContent !== error) errorEl.textContent = error;
    const button = _terminalComposeButtonFor(input, cellId);
    if (button) {
      const shouldDisable = !!disabled;
      if (button.disabled !== shouldDisable) button.disabled = shouldDisable;
    }
    return;
  }

  root.innerHTML = ''
    + '<form class="terminal-compose" data-cell-id="' + esc(cellId) + '"'
    + (directAgentId ? ' data-agent-id="' + esc(directAgentId) + '"' : '')
    + (replyToId ? ' data-reply-to-id="' + esc(replyToId) + '"' : '')
    + ' onsubmit="return terminalComposeSubmit(event, \'' + esc(cellId) + '\')">'
    + '  <div class="terminal-compose-input-wrap">'
    + replyHtml
    + '  <button type="button" class="terminal-compose-resize-handle"'
    + ' aria-label="Resize message composer" title="Drag to resize composer"'
    + ' onmousedown="return terminalComposeResizeStart(event, \'' + esc(cellId) + '\')"'
    + ' onkeydown="return terminalComposeResizeKeydown(event, \'' + esc(cellId) + '\')">'
    + '<span aria-hidden="true"></span></button>'
    + '  <div id="' + esc(inputId) + '" class="terminal-compose-input"'
    + ' contenteditable="true" role="textbox" aria-multiline="true"'
    + ' data-cell-id="' + esc(cellId) + '"'
    + (directAgentId ? ' data-agent-id="' + esc(directAgentId) + '"' : '')
    + ' data-placeholder="' + esc(placeholder) + '"'
    + ' aria-label="' + esc(placeholder) + '"'
    + ' oninput="terminalComposeInput(this)"'
    + ' onkeydown="terminalComposeKeydown(event, \'' + esc(cellId) + '\')"'
    + ' onpaste="return terminalComposePaste(event, \'' + esc(cellId) + '\')"'
    + ' ondragenter="terminalComposeDragenter(event, \'' + esc(cellId) + '\')"'
    + ' ondragover="terminalComposeDragover(event, \'' + esc(cellId) + '\')"'
    + ' ondragleave="terminalComposeDragleave(event, \'' + esc(cellId) + '\')"'
    + ' ondrop="terminalComposeDrop(event, \'' + esc(cellId) + '\')"></div>'
    + '  <div class="terminal-compose-error" aria-live="polite">' + esc(error) + '</div>'
    + '  <div id="' + esc(taskDropdownId) + '"'
    + ' class="deps-dropdown terminal-compose-task-dropdown ui-popover"'
    + ' role="listbox" aria-label="Matching tickets" style="display:none"></div>'
    + '  <div id="' + esc(slashDropdownId) + '"'
    + ' class="deps-dropdown terminal-compose-slash-dropdown ui-popover"'
    + ' role="listbox" aria-label="Slash commands" style="display:none"></div>'
    + '  </div>'
    + '  <button id="' + esc(buttonId) + '" class="terminal-compose-submit" type="submit"'
    + (disabled ? ' disabled' : '')
    + ' title="Send message">Send</button>'
    + '  <div class="terminal-compose-history-wrap">'
    + '    <button id="' + esc(historyButtonId) + '" class="terminal-compose-history-toggle" type="button"'
    + ' onclick="return terminalComposeHistoryToggle(event, \'' + esc(cellId) + '\')"'
    + ' title="Message history (use \u2191/\u2193 to recall)" aria-label="Message history"'
    + ' aria-haspopup="listbox" aria-expanded="false" aria-controls="' + esc(historyMenuId) + '">'
    + '<span class="terminal-compose-history-icon" aria-hidden="true">\u21ba</span></button>'
    + '    <div id="' + esc(historyMenuId) + '" class="terminal-compose-history-menu ui-popover"'
    + ' role="listbox" aria-label="Recent messages" hidden></div>'
    + '  </div>'
    + '</form>';
  const input = _terminalComposeTextarea(root);
  if (input) {
    _terminalComposeSetInputText(input, draft);
    _terminalComposeAutoResize(input);
    _terminalComposeSetButtonState(input);
  }
  const form = root.querySelector ? root.querySelector('.terminal-compose') : null;
  if (form) _terminalComposeRenderAttachmentChips(form, cellId);
}

function terminalComposeInput(el) {
  if (!el) return;
  const cellId = el.dataset ? (el.dataset.cellId || '') : '';
  if (cellId) _terminalComposeResetRecall(cellId);
  if (cellId) {
    const oldText = _terminalComposeDrafts[cellId] || '';
    const newText = _terminalComposeInputText(el);
    if (!_terminalComposeIsRichInput(el)) {
      _terminalComposeAdjustAttachmentPositions(cellId, oldText, newText);
    }
    _terminalComposePruneAttachments(cellId, newText);
    _terminalComposeDrafts[cellId] = newText;
    if (!_terminalComposeIsRichInput(el)) _terminalComposeRefreshAttachmentChips(cellId);
  }
  if (cellId && _terminalComposeErrors[cellId]) _terminalComposeSetError(el, '');
  _terminalComposeAutoResize(el);
  _terminalComposeSetButtonState(el);
  _terminalComposeUpdateAutocomplete(el);
}

function terminalComposeClear(cellId) {
  const id = String(cellId || '');
  const input = document.getElementById ? document.getElementById(_terminalComposeInputId(id)) : null;
  if (!input) return;
  _terminalComposeResetRecall(id);
  _terminalComposeHistoryClose(id);
  _terminalComposeSlashDropdownHide(id);
  _terminalComposeTaskDropdownHide(id);
  _terminalComposeSetInputText(input, '');
  if (id) _terminalComposeDrafts[id] = '';
  if (id && _terminalComposeAttachments[id] && Array.isArray(_terminalComposeAttachments[id].entries)) {
    _terminalComposeAttachments[id].entries.forEach(function(entry) {
      _terminalComposeRevokePreviewUrl(_terminalComposeAttachmentPreviewUrl(entry));
    });
  }
  if (id) delete _terminalComposeAttachments[id];
  if (id) delete _terminalComposeSelectedAttachmentByCell[id];
  _terminalComposeAutoResizeInvalidate(id);
  _terminalComposeAutoResize(input);
  _terminalComposeSetButtonState(input);
  _terminalComposeRefreshAttachmentChips(id);
  if (_terminalComposeIsRichInput(input)) _terminalComposeRenderRichInput(input);
}

function _terminalComposeActiveSelection(input) {
  if (!input) return 0;
  if (_terminalComposeIsRichInput(input)) return _terminalComposeSelectionOffsets(input).end;
  if (input.selectionDirection === 'backward' && typeof input.selectionStart === 'number') {
    return input.selectionStart;
  }
  return typeof input.selectionEnd === 'number' ? input.selectionEnd : 0;
}

function _terminalComposeSelectionAnchor(input) {
  if (!input) return 0;
  if (_terminalComposeIsRichInput(input)) {
    const selection = _terminalComposeSelectionOffsets(input);
    return selection.direction === 'backward' ? selection.end : selection.start;
  }
  if (input.selectionDirection === 'backward' && typeof input.selectionEnd === 'number') {
    return input.selectionEnd;
  }
  return typeof input.selectionStart === 'number' ? input.selectionStart : 0;
}

function _terminalComposeLineBoundary(value, caret, toEnd) {
  if (toEnd) {
    var lineEnd = value.indexOf('\n', caret);
    return lineEnd >= 0 ? lineEnd : value.length;
  }
  var lineStart = value.lastIndexOf('\n', caret > 0 ? caret - 1 : -1);
  return lineStart >= 0 ? lineStart + 1 : 0;
}

function _terminalComposeSetSelection(input, start, end, direction) {
  if (!input) return;
  if (_terminalComposeIsRichInput(input)) {
    _terminalComposeSetRichSelection(input, start, end, direction);
    return;
  }
  var valueLength = typeof input.value === 'string' ? input.value.length : 0;
  start = Math.max(0, Math.min(valueLength, start));
  end = Math.max(0, Math.min(valueLength, end));
  if (typeof input.setSelectionRange === 'function') {
    input.setSelectionRange(start, end, direction || 'none');
  } else {
    input.selectionStart = start;
    input.selectionEnd = end;
    if ('selectionDirection' in input) input.selectionDirection = direction || 'none';
  }
}

function _terminalComposeMoveCaret(input, evt, toEnd, wholeBuffer) {
  if (!input) return false;
  var value = _terminalComposeInputText(input);
  var active = _terminalComposeActiveSelection(input);
  var anchor = _terminalComposeSelectionAnchor(input);
  var target = wholeBuffer
    ? (toEnd ? value.length : 0)
    : _terminalComposeLineBoundary(value, active, toEnd);
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt.shiftKey) {
    _terminalComposeSetSelection(
      input,
      Math.min(anchor, target),
      Math.max(anchor, target),
      target < anchor ? 'backward' : 'forward'
    );
  } else {
    _terminalComposeSetSelection(input, target, target, 'none');
  }
  return true;
}

function _terminalComposeScrollToBottom(cellId) {
  const id = String(cellId || '');
  for (const key in _embeddedTerminalSessions) {
    const entry = _embeddedTerminalSessions[key];
    if (entry && entry.cellId === id) _embeddedTerminalScrollToTail(entry);
  }
}

function _terminalComposeDirectAgentForCellId(cellId) {
  const id = String(cellId || '').trim();
  const cell = id && state && state.agents ? state.agents[id] : null;
  return _terminalDirectMessageAgent(cell);
}

function _terminalComposeNextIdempotencyKey(agentId) {
  _terminalDirectMessageIdempotencyCounter += 1;
  return [
    'terminal-direct',
    String(agentId || 'agent'),
    Date.now(),
    _terminalDirectMessageIdempotencyCounter,
  ].join(':');
}

function _terminalComposeSendPayload(payload) {
  if (typeof send !== 'function') return false;
  try {
    const result = send(payload);
    return result !== false;
  } catch (_e) {
    return false;
  }
}

function _terminalValidateLoopComposerInput(input, text, directAgent) {
  const raw = String(text || '').trim();
  if (!(raw === '/loop' || raw.startsWith('/loop '))) return true;
  if (!directAgent || !directAgent.id) {
    _terminalComposeSetError(input, 'Select an agent to use /loop.');
    return false;
  }
  if (raw === '/loop cancel') return true;
  const match = raw.match(/^\/loop\s+every\s+(\S+)\s+([\s\S]+)$/);
  if (!match) {
    _terminalComposeSetError(input, 'Usage: /loop every 10m <message>, or /loop cancel.');
    return false;
  }
  const intervalMatch = String(match[1] || '').toLowerCase().match(/^(\d+)\s*([smh])$/);
  if (!intervalMatch) {
    _terminalComposeSetError(input, 'Loop interval must look like 1m, 10m, or 2h.');
    return false;
  }
  const amount = Number(intervalMatch[1] || 0) || 0;
  const unit = intervalMatch[2];
  const seconds = amount * (unit === 'h' ? 3600 : (unit === 'm' ? 60 : 1));
  if (seconds < 60) {
    _terminalComposeSetError(input, 'Loop interval must be at least 1m.');
    return false;
  }
  if (seconds > 86400) {
    _terminalComposeSetError(input, 'Loop interval must be 24h or less.');
    return false;
  }
  if (!String(match[2] || '').trim()) {
    _terminalComposeSetError(input, 'Loop message is required.');
    return false;
  }
  return true;
}

function terminalCancelUserMessageLoop(evt, agentId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  const id = String(agentId || '').trim();
  if (!id) return false;
  _terminalComposeSendPayload({
    cmd: 'user_agent_message',
    agent_id: id,
    message: '/loop cancel',
    thread_id: 'user-agent:user:' + id,
    idempotency_key: _terminalComposeNextIdempotencyKey(id),
  });
  return false;
}

function terminalComposeSubmit(evt, cellId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  const id = String(cellId || '');
  let input = null;
  if (evt && evt.currentTarget && evt.currentTarget.querySelector) {
    input = evt.currentTarget.querySelector('.terminal-compose-input');
  }
  if (!input && document.getElementById) {
    input = document.getElementById(_terminalComposeInputId(id));
  }
  if (!input) return false;
  const displayText = _terminalComposeInputText(input);
  const hasAttachments = _terminalComposeSortedAttachments(id).length > 0;
  if (!displayText.trim() && !hasAttachments) {
    terminalComposeClear(id);
    return false;
  }
  const text = _terminalComposePayloadText(id, displayText);
  const directAgent = _terminalComposeDirectAgentForCellId(id);
  if (!_terminalValidateLoopComposerInput(input, text, directAgent)) {
    _terminalComposeSetButtonState(input);
    return false;
  }
  let sent = false;
  let directAgentId = '';
  let replyToId = '';
  if (directAgent && directAgent.id) {
    directAgentId = String(directAgent.id || '');
    replyToId = String(_terminalDirectMessageReplyToByAgent[directAgentId] || '');
    const payload = {
      cmd: 'user_agent_message',
      agent_id: directAgentId,
      message: text,
      thread_id: 'user-agent:user:' + directAgentId,
      idempotency_key: _terminalComposeNextIdempotencyKey(directAgentId),
    };
    if (replyToId) payload.reply_to_id = replyToId;
    sent = _terminalComposeSendPayload(payload);
  } else {
    sent = _terminalComposeSendPayload({ cmd: 'send_user_message', cell_id: id, text: text });
  }
  if (!sent) {
    _terminalComposeSetError(input, 'Message was not sent — connection is unavailable. Please retry.');
    _terminalComposeSetButtonState(input);
    return false;
  }
  if (replyToId && directAgentId) delete _terminalDirectMessageReplyToByAgent[directAgentId];
  _terminalComposeScrollToBottom(id);
  terminalComposeClear(id);
  if (directAgent && typeof renderTerminalWorkspace === 'function') renderTerminalWorkspace();
  return false;
}

function terminalComposeKeydown(evt, cellId) {
  if (!evt) return;
  if (_terminalComposeHandleAttachmentDeleteKey(evt, cellId)) return;
  if (_terminalComposeSlashDropdownHandleKey(evt, cellId)) return;
  if (_terminalComposeTaskDropdownHandleKey(evt, cellId)) return;
  if (evt.key === 'Escape' && _terminalComposeHistoryIsOpen(cellId)) {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    _terminalComposeHistoryClose(cellId);
    return;
  }
  if ((evt.key === 'ArrowUp' || evt.key === 'ArrowDown')
      && !evt.shiftKey && !evt.ctrlKey && !evt.metaKey && !evt.altKey) {
    const input = evt.target && typeof evt.target.value === 'string'
      ? evt.target
      : (document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null);
    const id = String(cellId || (input && input.dataset ? input.dataset.cellId : '') || '');
    const recall = id ? _terminalComposeRecall[id] : null;
    const recallActive = !!(recall && recall.index >= 0);
    const direction = evt.key === 'ArrowUp' ? -1 : 1;
    const shouldRecall = (
      direction < 0
        ? _terminalComposeCaretAtFirstLine(input)
        : recallActive && _terminalComposeCaretAtLastLine(input)
    );
    if (shouldRecall && _terminalComposeHistoryNavigate(input, id, direction)) {
      if (typeof evt.preventDefault === 'function') evt.preventDefault();
      if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
      return;
    }
  }
  if ((evt.key === 'Home' || evt.key === 'End') && !evt.altKey) {
    const input = evt.target && typeof evt.target.value === 'string'
      ? evt.target
      : (document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null);
    if (_terminalComposeMoveCaret(input, evt, evt.key === 'End', !!(evt.metaKey || evt.ctrlKey))) {
      _terminalComposeUpdateAutocomplete(input);
      return;
    }
  }
  if (evt.key === 'Escape') {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    const input = evt.target && typeof evt.target.value === 'string'
      ? evt.target
      : (document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null);
    if (_terminalComposeRestoreRecallDraft(input, cellId)) {
      return;
    }
    terminalComposeClear(cellId);
    return;
  }
  if (evt.key === 'Enter' && !evt.shiftKey) {
    if (typeof evt.preventDefault === 'function') evt.preventDefault();
    if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    terminalComposeSubmit(evt, cellId);
  }
}

function terminalComposeDragenter(evt, cellId) {
  if (!evt || !_terminalComposeHasDraggedFiles(evt.dataTransfer)) return;
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  const input = evt.currentTarget || (
    document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null
  );
  _terminalComposeSetDropTarget(input, true);
}

function terminalComposeDragover(evt, cellId) {
  if (!evt || !_terminalComposeHasDraggedFiles(evt.dataTransfer)) return;
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt.dataTransfer) evt.dataTransfer.dropEffect = 'copy';
  const input = evt.currentTarget || (
    document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null
  );
  _terminalComposeSetDropTarget(input, true);
}

function terminalComposeDragleave(evt, cellId) {
  if (!evt || !_terminalComposeHasDraggedFiles(evt.dataTransfer)) return;
  const input = evt.currentTarget || (
    document.getElementById ? document.getElementById(_terminalComposeInputId(cellId)) : null
  );
  _terminalComposeSetDropTarget(input, false);
}

async function terminalComposeDrop(evt, cellId) {
  if (!evt || !_terminalComposeHasDraggedFiles(evt.dataTransfer)) return false;
  if (typeof evt.preventDefault === 'function') evt.preventDefault();
  if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
  const id = String(cellId || '');
  const input = evt.currentTarget || (
    document.getElementById ? document.getElementById(_terminalComposeInputId(id)) : null
  );
  _terminalComposeSetDropTarget(input, false);

  var files = _terminalComposeDroppedFiles(evt.dataTransfer);
  if (!files.length) {
    _terminalComposeSetError(input, 'Drop an image file to attach it.');
    return false;
  }
  var validation = terminalComposeValidateDroppedFiles(files);
  if (validation.errors.length) {
    _terminalComposeSetError(input, validation.errors[0]);
    return false;
  }
  if (!validation.accepted.length) {
    _terminalComposeSetError(input, 'Drop an image file to attach it.');
    return false;
  }

  try {
    _terminalComposeSetError(input, '');
    var attachments = await _terminalComposeUploadAttachments(id, validation.accepted);
    if (!attachments.length) {
      _terminalComposeSetError(input, 'Attachment upload failed.');
      return false;
    }
    _terminalComposeInsertAttachments(input, attachments);
  } catch (e) {
    _terminalComposeSetError(input, (e && e.message) || 'Attachment upload failed.');
  }
  return false;
}

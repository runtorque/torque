let _taskArtifactEditIndex = -1;
let _taskArtifactDraft = null;
var _artifactTypeLabels = {
  image: 'Image',
  file_ref: 'File ref',
  snippet: 'Snippet',
  log: 'Log',
  diff: 'Diff',
  test_report: 'Test report',
  generated_doc: 'Generated doc',
};

var _artifactPromptModeLabels = {
  auto: 'Auto',
  none: 'Skip prompt',
  path: 'Path',
  summary: 'Summary',
  inline: 'Inline',
};

function _artifactTypeLabel(type) {
  return _artifactTypeLabels[type] || (type || 'artifact');
}

function _artifactDefaultPromptMode(type) {
  if (type === 'image' || type === 'file_ref') return 'path';
  if (type === 'snippet') return 'inline';
  return 'summary';
}

function _artifactStorageKind(artifact) {
  if (!artifact) return 'inline';
  if (artifact.storage && artifact.storage.kind) return artifact.storage.kind;
  if (artifact.type === 'file_ref') return 'file_ref';
  if (artifact.path) return 'path';
  return 'inline';
}

function _artifactNormalizeClient(artifact, index) {
  var source = artifact || {};
  var path = source.path || ((source.storage || {}).path) || '';
  var content = source.content;
  if (content === undefined || content === null) {
    content = ((source.storage || {}).content) || '';
  }
  var type = source.type || 'file_ref';
  var lineStart = source.line_start;
  var lineEnd = source.line_end;
  var filename = source.filename || '';
  if (!filename && path) {
    var parts = String(path).split(/[\\/]/);
    filename = parts[parts.length - 1] || '';
  }
  return {
    id: source.id || ('artifact-' + (index + 1)),
    type: type,
    title: source.title || filename || _artifactTypeLabel(type),
    filename: filename,
    path: path,
    mime_type: source.mime_type || '',
    summary: source.summary || '',
    content: content || '',
    line_start: lineStart === undefined ? null : lineStart,
    line_end: lineEnd === undefined ? null : lineEnd,
    metadata: source.metadata || {},
    prompt: source.prompt || { mode: _artifactDefaultPromptMode(type) },
    provenance: source.provenance || {},
    storage: source.storage || {
      kind: _artifactStorageKind(source),
      path: path,
      content: content || '',
      line_start: lineStart === undefined ? null : lineStart,
      line_end: lineEnd === undefined ? null : lineEnd,
    },
    lifecycle: source.lifecycle || {},
    taskId: source.taskId || '',
    taskLabel: source.taskLabel || '',
  };
}

function _nextTaskArtifactId(artifacts) {
  var used = {};
  var greatest = 0;
  artifacts = artifacts || [];
  for (var i = 0; i < artifacts.length; i++) {
    var id = String((artifacts[i] || {}).id || '').trim();
    if (!id) continue;
    used[id] = true;
    var match = /^artifact-(\d+)$/.exec(id);
    if (match) greatest = Math.max(greatest, Number(match[1]));
  }
  var next = greatest + 1;
  while (used['artifact-' + next]) next++;
  return 'artifact-' + next;
}

function _artifactFromAttachment(attachment, taskId, taskLabel, index) {
  var a = attachment || {};
  return _artifactNormalizeClient({
    id: 'attachment-image-' + (index + 1),
    type: 'image',
    title: a.filename || 'image',
    filename: a.filename || '',
    path: a.path || '',
    mime_type: a.mime_type || 'image/png',
    metadata: { legacy_attachment: true },
    prompt: { mode: 'path' },
    storage: {
      kind: 'path',
      path: a.path || '',
      content: '',
      line_start: null,
      line_end: null,
    },
    lifecycle: { owner: 'task', cleanup: 'delete_with_task' },
    taskId: taskId || '',
    taskLabel: taskLabel || '',
  }, index);
}

function _taskArtifactsCombined(task) {
  var combined = [];
  task = task || {};
  var attachments = task.attachments || [];
  for (var i = 0; i < attachments.length; i++) {
    combined.push(_artifactFromAttachment(
      attachments[i],
      task.id || '',
      task.task || '',
      i,
    ));
  }
  var artifacts = task.artifacts || [];
  for (var j = 0; j < artifacts.length; j++) {
    var item = _artifactNormalizeClient(artifacts[j], combined.length + j);
    item.taskId = item.taskId || task.id || '';
    item.taskLabel = item.taskLabel || task.task || '';
    combined.push(item);
  }
  return combined;
}

function _artifactCountForTask(task) {
  return _taskArtifactsCombined(task).length;
}

function _artifactIsTextLike(artifact) {
  if (!artifact) return false;
  if (artifact.content) return true;
  var type = artifact.type || '';
  if (type === 'snippet' || type === 'log' || type === 'diff'
      || type === 'test_report' || type === 'generated_doc') {
    return true;
  }
  var mime = (artifact.mime_type || '').toLowerCase();
  return mime.indexOf('text/') === 0
    || mime.indexOf('json') >= 0
    || mime.indexOf('xml') >= 0
    || mime.indexOf('javascript') >= 0;
}

function _artifactPreviewText(artifact) {
  if (!artifact) return '';
  var text = artifact.content || artifact.summary || '';
  if (!text && artifact.path) text = artifact.path;
  text = String(text || '').trim();
  if (!text) return '';
  if (text.length > 480) return text.slice(0, 480) + '\n...';
  return text;
}

function _artifactStatsLabel(artifact) {
  var meta = (artifact && artifact.metadata) || {};
  if (meta.files || meta.insertions || meta.deletions) {
    return (meta.files || 0) + ' files, +' + (meta.insertions || 0)
      + '/-' + (meta.deletions || 0);
  }
  return '';
}

function _artifactFileUrl(taskId, artifact) {
  var effectiveTaskId = (artifact && artifact.taskId) || taskId || '';
  var filename = (artifact && artifact.filename) || '';
  if (!effectiveTaskId || !filename) return '';
  if (_artifactStorageKind(artifact) === 'inline' && !artifact.path) return '';
  return '/attachments/' + encodeURIComponent(effectiveTaskId) + '/'
    + encodeURIComponent(filename);
}

var _artifactPreviewRegistry = {};
var _artifactPreviewSequence = 0;
var _artifactPreviewOverlay = null;
var _artifactPreviewKeyHandler = null;
var _artifactPreviewToken = 0;

function _artifactPreviewRegister(taskId, artifact) {
  var normalized = _artifactNormalizeClient(artifact, _artifactPreviewSequence);
  normalized.taskId = normalized.taskId || taskId || '';
  var rawKey = [
    normalized.taskId || '',
    normalized.id || '',
    normalized.filename || '',
    normalized.path || '',
    normalized.title || '',
  ].join('\u001f');
  if (!rawKey.replace(/\u001f/g, '')) rawKey = 'artifact-preview-' + (++_artifactPreviewSequence);
  var key = encodeURIComponent(rawKey);
  if (_artifactPreviewRegistry[key]) key += '-' + (++_artifactPreviewSequence);
  _artifactPreviewRegistry[key] = {
    taskId: normalized.taskId || taskId || '',
    artifact: normalized,
  };
  return key;
}

function _artifactPreviewKind(artifact) {
  artifact = artifact || {};
  var mime = String(artifact.mime_type || '').toLowerCase().split(';')[0].trim();
  var name = String(artifact.filename || artifact.path || artifact.title || '').toLowerCase();
  var type = String(artifact.type || '').toLowerCase();
  if (mime.indexOf('image/') === 0 || type === 'image'
      || /\.(png|jpe?g|gif|webp|svg)$/.test(name)) {
    return 'image';
  }
  if (artifact.content || type === 'snippet' || type === 'log' || type === 'diff'
      || type === 'test_report' || type === 'generated_doc'
      || mime.indexOf('text/') === 0 || mime === 'application/json'
      || mime.indexOf('json') >= 0 || /\.(md|markdown|txt|diff|patch|json|log|csv|ya?ml|xml|html?|js|css)$/.test(name)) {
    return 'text';
  }
  return 'unsupported';
}

function _artifactPreviewDownloadHtml(url) {
  if (!url) return '';
  return '<a class="artifact-preview-download" href="' + esc(url)
    + '" download>Download</a>';
}

function _artifactPreviewShellHtml(artifact, bodyHtml, url) {
  var title = artifact.title || artifact.filename || 'Artifact preview';
  var meta = [];
  if (artifact.filename) meta.push(artifact.filename);
  if (artifact.mime_type) meta.push(artifact.mime_type);
  var metaHtml = meta.length
    ? '<div class="artifact-preview-meta">' + esc(meta.join(' · ')) + '</div>'
    : '';
  return '<div class="modal ui-modal ui-modal--full ui-modal--tall ui-modal--structured artifact-preview-modal" role="dialog" aria-modal="true" aria-labelledby="artifact-preview-title">'
    + '<div class="artifact-preview-head ui-modal__header ui-modal__header--bordered">'
    + '<div class="artifact-preview-title-wrap">'
    + '<h2 id="artifact-preview-title" class="ui-modal__title">' + esc(title) + '</h2>'
    + metaHtml
    + '</div>'
    + '<button class="artifact-preview-close" type="button" onclick="closeTaskArtifactPreview()" aria-label="Close">&times;</button>'
    + '</div>'
    + '<div class="artifact-preview-body ui-modal__body ui-modal__body--flush">' + bodyHtml + '</div>'
    + '<div class="modal-actions ui-modal__footer">'
    + _artifactPreviewDownloadHtml(url)
    + '<button class="btn-cancel" type="button" onclick="closeTaskArtifactPreview()">Close</button>'
    + '</div>'
    + '</div>';
}

function _artifactPreviewRender(artifact, bodyHtml, url, token) {
  if (token && token !== _artifactPreviewToken) return;
  if (!_artifactPreviewOverlay) return;
  _artifactPreviewOverlay.innerHTML = _artifactPreviewShellHtml(artifact, bodyHtml, url);
}

function closeTaskArtifactPreview() {
  _artifactPreviewToken++;
  if (_artifactPreviewKeyHandler) {
    document.removeEventListener('keydown', _artifactPreviewKeyHandler, true);
    _artifactPreviewKeyHandler = null;
  }
  if (_artifactPreviewOverlay) {
    _artifactPreviewOverlay.remove();
    _artifactPreviewOverlay = null;
  }
}

function _artifactPreviewCreateOverlay() {
  closeTaskArtifactPreview();
  var overlay = document.createElement('div');
  overlay.id = 'modal-artifact-preview';
  overlay.className = 'overlay artifact-preview-overlay visible';
  if (overlay.classList) overlay.classList.add('overlay', 'artifact-preview-overlay', 'visible');
  overlay.addEventListener('mousedown', function(e) {
    if (e.target === overlay) closeTaskArtifactPreview();
  });
  _artifactPreviewKeyHandler = function(e) {
    if (e.key !== 'Escape') return;
    if (e.preventDefault) e.preventDefault();
    if (e.stopPropagation) e.stopPropagation();
    closeTaskArtifactPreview();
  };
  document.addEventListener('keydown', _artifactPreviewKeyHandler, true);
  document.body.appendChild(overlay);
  _artifactPreviewOverlay = overlay;
  _artifactPreviewToken++;
  return overlay;
}

function _artifactPreviewUnsupportedHtml(url) {
  var download = _artifactPreviewDownloadHtml(url);
  return '<div class="artifact-preview-unsupported">Unsupported preview type'
    + (download ? ' — use the download link instead.' : '.')
    + '</div>';
}

function _artifactOpenPreview(taskId, artifact) {
  artifact = _artifactNormalizeClient(artifact || {}, 0);
  artifact.taskId = artifact.taskId || taskId || '';
  var url = _artifactFileUrl(taskId, artifact);
  var kind = _artifactPreviewKind(artifact);
  _artifactPreviewCreateOverlay();
  var token = _artifactPreviewToken;
  if (kind === 'image') {
    if (!url) {
      _artifactPreviewRender(artifact, _artifactPreviewUnsupportedHtml(url), url, token);
      return true;
    }
    var imageHtml = '<div class="artifact-preview-image-wrap">'
      + '<img class="artifact-preview-image" src="' + esc(url) + '" alt="'
      + esc(artifact.title || artifact.filename || 'artifact image') + '">'
      + '</div>';
    _artifactPreviewRender(artifact, imageHtml, url, token);
    return true;
  }
  if (kind === 'text') {
    var fallbackText = String(artifact.content || artifact.summary || artifact.path || '');
    var renderText = function(text) {
      _artifactPreviewRender(
        artifact,
        '<pre class="artifact-preview-pre">' + esc(String(text || '')) + '</pre>',
        url,
        token
      );
    };
    _artifactPreviewRender(artifact, '<div class="artifact-preview-loading ui-state ui-state--loading" role="status" aria-live="polite">Loading preview\u2026</div>', url, token);
    if (url && typeof fetch === 'function') {
      fetch(url)
        .then(function(response) {
          if (!response || !response.ok || typeof response.text !== 'function') {
            throw new Error('preview fetch failed');
          }
          return response.text();
        })
        .then(renderText)
        .catch(function() {
          if (fallbackText) renderText(fallbackText);
          else _artifactPreviewRender(
            artifact,
            '<div class="artifact-preview-unsupported">Could not load preview.</div>',
            url,
            token
          );
        });
    } else {
      renderText(fallbackText);
    }
    return true;
  }
  _artifactPreviewRender(artifact, _artifactPreviewUnsupportedHtml(url), url, token);
  return true;
}

function openTaskArtifactPreview(previewKey) {
  var entry = _artifactPreviewRegistry[previewKey];
  if (!entry) return false;
  return _artifactOpenPreview(entry.taskId, entry.artifact);
}

function _artifactHasOpenTarget(taskId, artifact) {
  return !!(_artifactFileUrl(taskId, artifact) || artifact.content || artifact.summary);
}

function _artifactMetaHtml(artifact) {
  var bits = [];
  bits.push('<span class="artifact-chip artifact-chip-type">'
    + esc(_artifactTypeLabel(artifact.type)) + '</span>');
  var promptMode = ((artifact.prompt || {}).mode) || _artifactDefaultPromptMode(artifact.type);
  bits.push('<span class="artifact-chip">' + esc(_artifactPromptModeLabels[promptMode] || promptMode) + '</span>');
  bits.push('<span class="artifact-chip">' + esc(_artifactStorageKind(artifact)) + '</span>');
  if (artifact.mime_type) {
    bits.push('<span class="artifact-chip">' + esc(artifact.mime_type) + '</span>');
  }
  if (artifact.line_start || artifact.line_end) {
    var lineLabel = 'L' + esc(String(artifact.line_start || '?'));
    if (artifact.line_end && artifact.line_end !== artifact.line_start) {
      lineLabel += '-' + esc(String(artifact.line_end));
    }
    bits.push('<span class="artifact-chip">' + lineLabel + '</span>');
  }
  var stats = _artifactStatsLabel(artifact);
  if (stats) bits.push('<span class="artifact-chip">' + esc(stats) + '</span>');
  return bits.join('');
}

function _renderArtifactCard(artifact, opts) {
  opts = opts || {};
  var taskId = opts.taskId || artifact.taskId || '';
  var html = '<div class="artifact-card">';
  html += '<div class="artifact-card-head">';
  html += '<div class="artifact-card-title-wrap">';
  html += '<div class="artifact-card-title">' + esc(artifact.title || artifact.filename || 'Artifact') + '</div>';
  if (artifact.taskLabel && opts.showTaskLabel) {
    html += '<div class="artifact-card-task">' + esc(artifact.taskLabel) + '</div>';
  } else if (artifact.path) {
    html += '<div class="artifact-card-path">' + esc(artifact.path) + '</div>';
  }
  html += '</div>';
  html += '<div class="artifact-card-actions">';
  var url = _artifactFileUrl(taskId, artifact);
  if (_artifactHasOpenTarget(taskId, artifact)) {
    var previewKey = _artifactPreviewRegister(taskId, artifact);
    html += '<button type="button" class="artifact-card-action" data-artifact-preview-key="' + esc(previewKey)
      + '" onclick="event.stopPropagation();openTaskArtifactPreview(this.dataset.artifactPreviewKey)">Open</button>';
  }
  if (opts.onEdit) {
    html += '<button class="artifact-card-action" onclick="' + opts.onEdit + '">Edit</button>';
  }
  if (opts.onRemove) {
    html += '<button class="artifact-card-action artifact-card-action-danger" onclick="' + opts.onRemove + '">Delete</button>';
  }
  html += '</div>';
  html += '</div>';
  html += '<div class="artifact-card-meta">' + _artifactMetaHtml(artifact) + '</div>';
  if (artifact.summary) {
    html += '<div class="artifact-card-summary">' + esc(artifact.summary) + '</div>';
  }
  if (artifact.type === 'image' && url) {
    html += '<div class="artifact-card-image"><img src="' + esc(url)
      + '" alt="' + esc(artifact.title || artifact.filename || 'artifact image') + '"></div>';
  } else {
    var preview = _artifactPreviewText(artifact);
    if (preview) {
      html += '<pre class="artifact-card-preview">' + esc(preview) + '</pre>';
    }
  }
  html += '</div>';
  return html;
}

function _renderArtifactCollection(artifacts, opts) {
  opts = opts || {};
  if (!artifacts || !artifacts.length) {
    return '<div class="artifact-empty ui-state ui-state--empty ui-state--compact">' + esc(opts.empty || 'No artifacts attached.') + '</div>';
  }
  var html = '<div class="artifact-collection">';
  for (var i = 0; i < artifacts.length; i++) {
    html += _renderArtifactCard(artifacts[i], opts.cardOptions || {});
  }
  html += '</div>';
  return html;
}

function _artifactDraftForType(type) {
  var resolvedType = type || 'snippet';
  var storageKind = resolvedType === 'file_ref' ? 'file_ref' : 'inline';
  var draft = _artifactNormalizeClient({
    type: resolvedType,
    title: '',
    summary: '',
    content: '',
    path: '',
    line_start: null,
    line_end: null,
    metadata: {},
    prompt: { mode: _artifactDefaultPromptMode(resolvedType) },
    storage: {
      kind: storageKind,
      path: '',
      content: '',
      line_start: null,
      line_end: null,
    },
  }, _taskArtifacts.length);
  // New drafts must receive their ID at save time, after examining the
  // current collection. This avoids reusing a gapped or high suffix.
  delete draft.id;
  return draft;
}

function _taskArtifactUploadId() {
  return _taskEditId || _taskDraftId;
}

function _artifactClone(artifact) {
  return JSON.parse(JSON.stringify(_artifactNormalizeClient(artifact, 0)));
}
/* -- Task modal: artifact helpers ---------------------------------------- */

function _inferArtifactTypeFromFile(name, mime) {
  var lowerName = String(name || '').toLowerCase();
  var lowerMime = String(mime || '').toLowerCase();
  if (lowerMime.indexOf('image/') === 0) return 'image';
  if (/\.(diff|patch)$/.test(lowerName)) return 'diff';
  if (/(^|[._-])(pytest|junit|tap|coverage|report|results?)([._-]|$)/.test(lowerName)) {
    return 'test_report';
  }
  if (/\.(log|out|err|trace|txt)$/.test(lowerName)) return 'log';
  if (/\.(md|markdown|html|htm|json|yaml|yml|xml|csv)$/.test(lowerName)) {
    return 'generated_doc';
  }
  if (lowerMime.indexOf('text/') === 0 || lowerMime.indexOf('json') >= 0 || lowerMime.indexOf('xml') >= 0) {
    return 'generated_doc';
  }
  return 'file_ref';
}

async function _artifactReadPreviewText(file) {
  if (!file || typeof file.text !== 'function' || file.size > 262144) return '';
  try {
    return await file.text();
  } catch (err) {
    return '';
  }
}

function _artifactSummaryFromFile(file, type) {
  var parts = [];
  if (file && typeof file.size === 'number') {
    if (file.size >= 1024 * 1024) parts.push((file.size / (1024 * 1024)).toFixed(1) + ' MB');
    else if (file.size >= 1024) parts.push(Math.round(file.size / 1024) + ' KB');
    else parts.push(file.size + ' B');
  }
  if (type === 'diff') parts.push('uploaded patch');
  else if (type === 'log') parts.push('uploaded log');
  else if (type === 'test_report') parts.push('uploaded report');
  return parts.join(' | ');
}

function _artifactFromUploadedFile(entry, file, content, existingArtifacts) {
  var type = _inferArtifactTypeFromFile(entry.filename || file.name || '', entry.mime_type || file.type || '');
  var normalized = _artifactNormalizeClient({
    id: _nextTaskArtifactId(existingArtifacts),
    type: type,
    title: entry.filename || file.name || 'artifact',
    filename: entry.filename || file.name || '',
    path: entry.path || '',
    mime_type: entry.mime_type || file.type || '',
    summary: _artifactSummaryFromFile(file, type),
    content: _artifactIsTextLike({ type: type, mime_type: entry.mime_type || file.type || '', content: content || '' }) ? (content || '') : '',
    prompt: { mode: _artifactDefaultPromptMode(type) },
    metadata: {
      size_bytes: entry.size_bytes || file.size || 0,
    },
    storage: {
      kind: type === 'file_ref' ? 'file_ref' : 'path',
      path: entry.path || '',
      content: _artifactIsTextLike({ type: type, mime_type: entry.mime_type || file.type || '', content: content || '' }) ? (content || '') : '',
      line_start: null,
      line_end: null,
    },
    lifecycle: { owner: 'task', cleanup: 'delete_with_task' },
  }, _taskArtifacts.length);
  normalized.taskId = _taskArtifactUploadId();
  return normalized;
}

function _renderTaskArtifacts() {
  var container = document.getElementById('task-artifacts-list');
  if (!container) return;
  var artifacts = [];
  for (var i = 0; i < _taskArtifacts.length; i++) {
    var item = _artifactNormalizeClient(_taskArtifacts[i], i);
    item.taskId = item.taskId || _taskArtifactUploadId();
    artifacts.push(item);
  }
  if (!artifacts.length) {
    container.innerHTML = '<div class="artifact-empty ui-state ui-state--empty ui-state--compact">No logs, reports, diffs, or references yet.</div>';
    return;
  }
  var html = '<div class="artifact-collection">';
  for (var j = 0; j < artifacts.length; j++) {
    html += _renderArtifactCard(artifacts[j], {
      taskId: _taskArtifactUploadId(),
      onEdit: 'taskArtifactEdit(' + j + ')',
      onRemove: 'taskArtifactRemove(' + j + ')',
    });
  }
  html += '</div>';
  container.innerHTML = html;
}

function _renderTaskArtifactEditor() {
  var container = document.getElementById('task-artifact-editor');
  if (!container) return;
  if (_taskArtifactEditIndex < 0 || !_taskArtifactDraft) {
    container.innerHTML = '';
    container.classList.remove('visible');
    return;
  }
  var draft = _taskArtifactDraft;
  var html = '<div class="task-artifact-editor-card">';
  html += '<div class="task-artifact-editor-title">'
    + (_taskArtifactEditIndex < _taskArtifacts.length ? 'Edit artifact' : 'New artifact')
    + '</div>';
  html += '<div class="task-artifact-editor-grid">';
  html += '<label>Type</label>';
  html += '<select onchange="taskArtifactDraftChange(\'type\', this.value)">';
  var types = ['snippet', 'log', 'diff', 'test_report', 'generated_doc', 'file_ref', 'image'];
  for (var i = 0; i < types.length; i++) {
    html += '<option value="' + esc(types[i]) + '"' + (draft.type === types[i] ? ' selected' : '') + '>'
      + esc(_artifactTypeLabel(types[i])) + '</option>';
  }
  html += '</select>';
  html += '<label>Title</label>';
  html += '<input value="' + esc(draft.title || '') + '" oninput="taskArtifactDraftChange(\'title\', this.value)" placeholder="e.g. pytest log">';
  html += '<label>Summary</label>';
  html += '<textarea rows="2" oninput="taskArtifactDraftChange(\'summary\', this.value);taskAutoResize(this)" placeholder="What this artifact is for...">'
    + esc(draft.summary || '') + '</textarea>';
  html += '<label>Prompt mode</label>';
  html += '<select onchange="taskArtifactDraftChange(\'prompt_mode\', this.value)">';
  var promptModes = ['auto', 'path', 'summary', 'inline', 'none'];
  var currentPrompt = ((draft.prompt || {}).mode) || _artifactDefaultPromptMode(draft.type);
  for (var j = 0; j < promptModes.length; j++) {
    html += '<option value="' + esc(promptModes[j]) + '"' + (currentPrompt === promptModes[j] ? ' selected' : '') + '>'
      + esc(_artifactPromptModeLabels[promptModes[j]]) + '</option>';
  }
  html += '</select>';
  html += '<label>Path</label>';
  html += '<input value="' + esc(draft.path || '') + '" oninput="taskArtifactDraftChange(\'path\', this.value)" placeholder="/repo/path/to/file.log">';
  html += '<label>Line start</label>';
  html += '<input value="' + esc(draft.line_start || '') + '" oninput="taskArtifactDraftChange(\'line_start\', this.value)" placeholder="12">';
  html += '<label>Line end</label>';
  html += '<input value="' + esc(draft.line_end || '') + '" oninput="taskArtifactDraftChange(\'line_end\', this.value)" placeholder="24">';
  html += '<label>Content</label>';
  html += '<textarea rows="6" oninput="taskArtifactDraftChange(\'content\', this.value);taskAutoResize(this)" placeholder="Paste a useful excerpt, command output, or report summary...">'
    + esc(draft.content || '') + '</textarea>';
  html += '</div>';
  html += '<div class="modal-actions ui-modal__footer">';
  html += '<button class="btn-cancel" type="button" onclick="taskArtifactCancelEdit()">Cancel</button>';
  html += '<button class="btn-primary" type="button" onclick="taskArtifactSave()">Save Artifact</button>';
  html += '</div></div>';
  container.innerHTML = html;
  container.classList.add('visible');
  container.querySelectorAll('textarea').forEach(taskAutoResize);
}

function taskArtifactPickFiles() {
  var input = document.getElementById('task-artifact-upload-input');
  if (input) input.click();
}

async function _taskUploadArtifactFiles(files) {
  var tid = _taskArtifactUploadId();
  if (!tid || !files || !files.length) return;
  for (var i = 0; i < files.length; i++) {
    var file = files[i];
    var fd = new FormData();
    fd.append('task_id', tid);
    fd.append('file', file);
    try {
      var parts = await Promise.all([
        fetch('/api/upload', { method: 'POST', body: fd }).then(function(r) { return r.json(); }),
        _artifactReadPreviewText(file),
      ]);
      var response = parts[0];
      var previewText = parts[1];
      if (response.ok && response.data && response.data.length) {
        for (var j = 0; j < response.data.length; j++) {
          _taskArtifacts.push(_artifactFromUploadedFile(
            response.data[j], file, previewText, _taskArtifacts
          ));
        }
      }
    } catch (err) {
      // Keep failure silent in the UI flow for now; upload endpoint errors surface in logs.
    }
  }
  _renderTaskArtifacts();
}

function taskArtifactFilePicked(input) {
  var files = input && input.files ? Array.from(input.files) : [];
  input.value = '';
  _taskUploadArtifactFiles(files);
}

function taskArtifactStart(type) {
  _taskArtifactEditIndex = _taskArtifacts.length;
  _taskArtifactDraft = _artifactDraftForType(type);
  _renderTaskArtifactEditor();
}

function taskArtifactEdit(index) {
  var artifact = _taskArtifacts[index];
  if (!artifact) return;
  _taskArtifactEditIndex = index;
  _taskArtifactDraft = _artifactClone(artifact);
  _renderTaskArtifactEditor();
}

function taskArtifactDraftChange(field, value) {
  if (!_taskArtifactDraft) return;
  if (field === 'type') {
    _taskArtifactDraft.type = value || 'snippet';
    if (!_taskArtifactDraft.title && _taskArtifactDraft.filename) {
      _taskArtifactDraft.title = _taskArtifactDraft.filename;
    }
    _taskArtifactDraft.prompt = _taskArtifactDraft.prompt || {};
    _taskArtifactDraft.prompt.mode = _artifactDefaultPromptMode(_taskArtifactDraft.type);
    _taskArtifactDraft.storage = _taskArtifactDraft.storage || {};
    _taskArtifactDraft.storage.kind = _artifactStorageKind(_taskArtifactDraft);
  } else if (field === 'prompt_mode') {
    _taskArtifactDraft.prompt = _taskArtifactDraft.prompt || {};
    _taskArtifactDraft.prompt.mode = value || _artifactDefaultPromptMode(_taskArtifactDraft.type);
  } else if (field === 'line_start' || field === 'line_end') {
    _taskArtifactDraft[field] = value === '' ? null : Number(value);
    _taskArtifactDraft.storage = _taskArtifactDraft.storage || {};
    _taskArtifactDraft.storage[field] = _taskArtifactDraft[field];
  } else {
    _taskArtifactDraft[field] = value;
    if (field === 'path' || field === 'content') {
      _taskArtifactDraft.storage = _taskArtifactDraft.storage || {};
      _taskArtifactDraft.storage[field] = value;
      _taskArtifactDraft.storage.kind = _artifactStorageKind(_taskArtifactDraft);
    }
  }
}

function taskArtifactCancelEdit() {
  _taskArtifactEditIndex = -1;
  _taskArtifactDraft = null;
  _renderTaskArtifactEditor();
}

function taskArtifactSave() {
  if (!_taskArtifactDraft) return;
  _taskArtifactDraft.storage = _taskArtifactDraft.storage || {};
  _taskArtifactDraft.storage.kind = _artifactStorageKind(_taskArtifactDraft);
  var source = _artifactClone(_taskArtifactDraft);
  if (_taskArtifactEditIndex >= _taskArtifacts.length || !source.id) {
    source.id = _nextTaskArtifactId(_taskArtifacts);
  }
  var artifact = _artifactNormalizeClient(source, _taskArtifactEditIndex);
  artifact.taskId = _taskArtifactUploadId();
  if (!artifact.title) {
    artifact.title = artifact.filename || (artifact.path ? artifact.path.split(/[\\/]/).pop() : _artifactTypeLabel(artifact.type));
  }
  if (artifact.type === 'file_ref' && !artifact.path) return;
  if (!artifact.title && !artifact.path && !artifact.content) return;
  if (_taskArtifactEditIndex >= _taskArtifacts.length) _taskArtifacts.push(artifact);
  else _taskArtifacts[_taskArtifactEditIndex] = artifact;
  _taskArtifactEditIndex = -1;
  _taskArtifactDraft = null;
  _renderTaskArtifacts();
  _renderTaskArtifactEditor();
}

function _artifactOwnedUpload(artifact) {
  artifact = artifact || {};
  return !!(artifact.filename && ((artifact.lifecycle || {}).owner || 'task') === 'task');
}

function taskArtifactRemove(index) {
  var artifact = _taskArtifacts[index];
  if (!artifact) return;
  _taskArtifacts.splice(index, 1);
  if (_taskArtifactEditIndex === index) {
    _taskArtifactEditIndex = -1;
    _taskArtifactDraft = null;
  } else if (_taskArtifactEditIndex > index) {
    _taskArtifactEditIndex--;
  }
  _renderTaskArtifacts();
  _renderTaskArtifactEditor();
}

function _artifactRemovedFiles(originalArtifacts, currentArtifacts) {
  var keep = {};
  for (var i = 0; i < currentArtifacts.length; i++) {
    var current = currentArtifacts[i];
    if (_artifactOwnedUpload(current) && current.filename) keep[current.filename] = true;
  }
  var removed = [];
  for (var j = 0; j < originalArtifacts.length; j++) {
    var original = originalArtifacts[j];
    if (_artifactOwnedUpload(original) && original.filename && !keep[original.filename]) {
      removed.push(original.filename);
    }
  }
  return removed;
}

function openTaskArtifactBrowser(taskId) {
  var tasks = (state && state.board_tasks) || {};
  var task = tasks[taskId];
  if (!task) return;
  // Compact cards don't carry attachments/artifacts — hydrate before
  // rendering so the browser doesn't falsely report "no artifacts".
  if (typeof ensureTaskDetail === 'function'
      && typeof _compactModeActive === 'function'
      && _compactModeActive()
      && typeof _compactTaskHasFullDetail === 'function'
      && !_compactTaskHasFullDetail(task)) {
    ensureTaskDetail(taskId, function() { openTaskArtifactBrowser(taskId); });
    return;
  }
  var artifacts = _taskArtifactsCombined(task);
  document.getElementById('task-artifacts-modal-title').textContent = 'Artifacts - ' + (task.task || '').slice(0, 80);
  document.getElementById('task-artifacts-modal-summary').textContent =
    artifacts.length ? (artifacts.length + ' artifact' + (artifacts.length === 1 ? '' : 's')) : 'No artifacts attached.';
  document.getElementById('task-artifacts-modal-content').innerHTML = _renderArtifactCollection(artifacts, {
    empty: 'No artifacts attached to this task.',
    cardOptions: { taskId: task.id },
  });
  document.getElementById('modal-task-artifacts').classList.add('visible');
}

function _findTaskArtifact(taskId, artifactId, filename, artifactPath) {
  if (!taskId) return null;
  var tasks = (state && state.board_tasks) || {};
  var task = tasks[taskId];
  if (!task) return null;
  var artifacts = _taskArtifactsCombined(task);
  var targetId = String(artifactId || '').trim();
  var targetFilename = String(filename || '').trim();
  var targetPath = String(artifactPath || '').trim();
  if (!targetId && !targetFilename && !targetPath) return null;
  var candidates = [];
  for (var i = 0; i < artifacts.length; i++) {
    var artifact = artifacts[i];
    var artifactIdValue = String((artifact && artifact.id) || '').trim();
    var artifactFilename = String((artifact && artifact.filename) || '').trim();
    var artifactPathValue = String(
      (artifact && artifact.path)
      || (((artifact || {}).storage || {}).path)
      || ''
    ).trim();
    if (targetId && artifactIdValue !== targetId) continue;
    if (targetFilename && artifactFilename !== targetFilename) continue;
    if (targetPath && artifactPathValue !== targetPath) continue;
    candidates.push(artifact);
  }
  return candidates.length === 1 ? candidates[0] : null;
}

function openTaskArtifactById(taskId, artifactId, filename, artifactPath) {
  // Deep-links can target artifacts that live only on the full BoardTask.
  // If the local card is a compact summary, hydrate and retry before
  // falling back to the empty browser.
  var tasks = (state && state.board_tasks) || {};
  var task = tasks[taskId];
  if (task
      && typeof ensureTaskDetail === 'function'
      && typeof _compactModeActive === 'function'
      && _compactModeActive()
      && typeof _compactTaskHasFullDetail === 'function'
      && !_compactTaskHasFullDetail(task)) {
    ensureTaskDetail(taskId, function() {
      openTaskArtifactById(taskId, artifactId, filename, artifactPath);
    });
    return true;
  }
  var artifact = _findTaskArtifact(taskId, artifactId, filename, artifactPath);
  if (!artifact) return false;
  return _artifactOpenPreview(taskId, artifact);
}

/* -- Task modal: attachment helpers -------------------------------------- */

function _generateDraftId() {
  var hex = '';
  for (var i = 0; i < 8; i++) hex += Math.floor(Math.random() * 16).toString(16);
  return 'draft-' + hex;
}

function _taskAttId() {
  return _taskEditId || _taskDraftId;
}

function _uploadFiles(files) {
  var tid = _taskAttId();
  if (!tid) return;
  for (var i = 0; i < files.length; i++) {
    var file = files[i];
    if (!file.type.startsWith('image/')) continue;
    var fd = new FormData();
    fd.append('task_id', tid);
    fd.append('file', file);
    fetch('/api/upload', { method: 'POST', body: fd })
      .then(function(r) { return r.json(); })
      .then(function(res) {
        if (res.ok && res.data) {
          for (var j = 0; j < res.data.length; j++) {
            _taskAttachments.push(res.data[j]);
          }
          _renderTaskAttachments();
          taskPersistDraft();
        }
      });
  }
}

function taskAttFilePicked(input) {
  if (input.files && input.files.length) _uploadFiles(input.files);
  input.value = '';
}

function taskAttDragOver(e) {
  e.preventDefault();
  e.currentTarget.classList.add('drag-over');
}

function taskAttDragLeave(e) {
  e.currentTarget.classList.remove('drag-over');
}

function taskAttDrop(e) {
  e.preventDefault();
  e.currentTarget.classList.remove('drag-over');
  if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
    _uploadFiles(e.dataTransfer.files);
  }
}

function taskAttRemove(idx) {
  var att = _taskAttachments[idx];
  if (!att) return;
  _taskAttachments.splice(idx, 1);
  _renderTaskAttachments();
  taskPersistDraft();
}

function _renderTaskAttachments() {
  var container = document.getElementById('task-attachments-thumbs');
  if (!container) return;
  var html = '';
  var tid = _taskAttId();
  for (var i = 0; i < _taskAttachments.length; i++) {
    var a = _taskAttachments[i];
    var src = '/attachments/' + encodeURIComponent(tid) + '/' + encodeURIComponent(a.filename);
    html += '<div class="attachment-thumb">'
      + '<img src="' + src + '" alt="' + esc(a.filename) + '" title="' + esc(a.filename) + '">'
      + '<button type="button" class="attachment-remove" aria-label="Remove attachment ' + esc(a.filename) + '" onclick="taskAttRemove(' + i + ')">&times;</button>'
      + '</div>';
  }
  container.innerHTML = html;
}

function _cleanupDraftAttachments() {
  var hasDraftUploads = _taskAttachments.length > 0;
  if (!hasDraftUploads) {
    for (var di = 0; di < _taskArtifacts.length; di++) {
      if (_artifactOwnedUpload(_taskArtifacts[di])) {
        hasDraftUploads = true;
        break;
      }
    }
  }
  if (_taskDraftId && hasDraftUploads && !_taskEditId) {
    // Create mode: wipe the whole draft dir
    fetch('/api/upload/cleanup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: _taskDraftId })
    });
  } else if (_taskEditId) {
    // Edit mode: remove newly uploaded files not in original set
    var origNames = {};
    for (var i = 0; i < _taskOriginalAttachments.length; i++) {
      origNames[_taskOriginalAttachments[i].filename] = true;
    }
    for (var i = 0; i < _taskAttachments.length; i++) {
      if (!origNames[_taskAttachments[i].filename]) {
        send({ cmd: 'remove_attachment', task_id: _taskEditId, filename: _taskAttachments[i].filename });
      }
    }
    var origArtifactNames = {};
    for (var j = 0; j < _taskOriginalArtifacts.length; j++) {
      if (_artifactOwnedUpload(_taskOriginalArtifacts[j]) && _taskOriginalArtifacts[j].filename) {
        origArtifactNames[_taskOriginalArtifacts[j].filename] = true;
      }
    }
    for (var k = 0; k < _taskArtifacts.length; k++) {
      if (_artifactOwnedUpload(_taskArtifacts[k]) && _taskArtifacts[k].filename
          && !origArtifactNames[_taskArtifacts[k].filename]) {
        send({ cmd: 'remove_attachment', task_id: _taskEditId, filename: _taskArtifacts[k].filename });
      }
    }
  }
}

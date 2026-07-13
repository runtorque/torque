/* Shared settings workspace behavior.
 *
 * Group and application settings intentionally keep their domain-specific
 * read/write code in their existing modules. This file owns only the shared
 * dialog shell: dirty state, search, section reset, close protection, and
 * client-local appearance preferences.
 */

var _settingsShellState = Object.create(null);
var _settingsAppearanceKey = 'torque.appearance.v1';
var _settingsAppearanceAccent = 'blue';

function _settingsShellModal(modalOrId) {
  if (!modalOrId) return null;
  if (typeof modalOrId !== 'string') return modalOrId;
  return document.getElementById(modalOrId);
}

function _settingsShellControls(root) {
  if (!root || !root.querySelectorAll) return [];
  return Array.prototype.slice.call(root.querySelectorAll('input, select, textarea'))
    .filter(function(el) {
      return el && el.type !== 'search' && el.type !== 'button' && el.type !== 'submit';
    });
}

function _settingsShellControlKey(el, index) {
  return el.id || el.name || ('control-' + index);
}

function _settingsShellControlValue(el) {
  if (el.type === 'checkbox' || el.type === 'radio') return !!el.checked;
  return String(el.value == null ? '' : el.value);
}

function _settingsShellSerialize(root) {
  var values = {};
  _settingsShellControls(root).forEach(function(el, index) {
    values[_settingsShellControlKey(el, index)] = _settingsShellControlValue(el);
  });
  return values;
}

function _settingsShellEqual(left, right) {
  var a = left || {};
  var b = right || {};
  var aKeys = Object.keys(a);
  var bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  return aKeys.every(function(key) { return a[key] === b[key]; });
}

function _settingsShellSaveButtons(modal) {
  if (!modal || !modal.querySelectorAll) return [];
  return Array.prototype.slice.call(modal.querySelectorAll(
    '#gs-save-btn, #gls-global-save-btn, #gls-ai-save-btn'
  ));
}

function _settingsShellStateLabel(modal) {
  return modal && modal.querySelector
    ? modal.querySelector('.settings-save-state')
    : null;
}

function _settingsShellApplyDirty(modal, dirty) {
  if (!modal) return;
  var state = _settingsShellState[modal.id] || (_settingsShellState[modal.id] = {});
  state.dirty = !!dirty;
  if (modal.classList) modal.classList.toggle('is-dirty', !!dirty);
  var dialog = modal.querySelector && modal.querySelector('.settings-dialog');
  if (dialog && dialog.classList) dialog.classList.toggle('is-dirty', !!dirty);
  var label = _settingsShellStateLabel(modal);
  if (label) label.textContent = dirty ? 'Unsaved changes' : 'No unsaved changes';
  _settingsShellSaveButtons(modal).forEach(function(button) {
    button.disabled = !dirty;
  });
}

function settingsShellMarkDirty(modalOrId) {
  var modal = _settingsShellModal(modalOrId);
  if (!modal) return;
  var state = _settingsShellState[modal.id];
  if (!state || !state.baseline) return;
  _settingsShellApplyDirty(modal, !_settingsShellEqual(
    state.baseline,
    _settingsShellSerialize(modal)
  ));
}

function settingsShellForceDirty(modalOrId) {
  var modal = _settingsShellModal(modalOrId);
  if (modal) _settingsShellApplyDirty(modal, true);
}

function _settingsShellInstallListeners(modal) {
  if (!modal || modal.dataset && modal.dataset.settingsShellBound === '1') return;
  var mark = function(event) {
    var target = event && event.target;
    if (!target || target.type === 'search') return;
    if (!/^(INPUT|SELECT|TEXTAREA)$/.test(String(target.tagName || ''))) return;
    if (modal.id === 'modal-group-settings') _settingsShellUpdateInheritance(target);
    settingsShellMarkDirty(modal);
  };
  if (modal.addEventListener) {
    modal.addEventListener('input', mark, true);
    modal.addEventListener('change', mark, true);
  }
  if (modal.dataset) modal.dataset.settingsShellBound = '1';
}

function settingsShellCaptureBaseline(modalOrId) {
  var modal = _settingsShellModal(modalOrId);
  if (!modal) return;
  _settingsShellInstallListeners(modal);
  if (modal.id === 'modal-group-settings') settingsShellDecorateInheritance();
  var state = _settingsShellState[modal.id] || (_settingsShellState[modal.id] = {});
  state.baseline = _settingsShellSerialize(modal);
  _settingsShellApplyDirty(modal, false);
  var search = modal.querySelector && modal.querySelector('input[type="search"]');
  if (search) search.value = '';
  settingsShellSearch(modal.id, '');
  settingsShellSyncView(modal.id);
}

function _settingsShellInheritanceSource(control) {
  if (!control) return '';
  if (/^gs-(worker|engineer|architect)-/.test(control.id || '')) return 'Group default';
  return '';
}

function _settingsShellIsInherited(control) {
  if (!control) return false;
  if (control.tagName === 'SELECT') return !String(control.value || '');
  return !String(control.value || '').trim();
}

function _settingsShellUpdateInheritance(control) {
  if (!control || !control.parentNode) return;
  var note = control.parentNode.querySelector
    ? control.parentNode.querySelector('.settings-inheritance-note[data-for="' + control.id + '"]')
    : null;
  if (!note) return;
  var inherited = _settingsShellIsInherited(control);
  note.classList.toggle('is-overridden', !inherited);
  var copy = note.querySelector('.settings-inheritance-copy');
  var reset = note.querySelector('button');
  if (copy) copy.textContent = inherited
    ? 'Inherited from Group'
    : 'Override active for this agent kind';
  if (reset) reset.hidden = inherited;
}

function settingsShellUseInherited(controlId) {
  var control = document.getElementById(controlId);
  if (!control) return;
  control.value = '';
  _settingsShellUpdateInheritance(control);
  settingsShellMarkDirty('modal-group-settings');
  if (control.focus) control.focus();
}

function settingsShellDecorateInheritance() {
  var modal = document.getElementById('modal-group-settings');
  if (!modal || !modal.querySelectorAll || !document.createElement) return;
  modal.querySelectorAll('input, select').forEach(function(control) {
    var source = _settingsShellInheritanceSource(control);
    if (!source || !control.id || !control.parentNode || !control.parentNode.insertBefore) return;
    var eligible = false;
    if (control.tagName === 'SELECT') {
      var first = control.options && control.options.length ? control.options[0] : null;
      eligible = !!(first && /group default/i.test(first.textContent || ''));
    } else {
      eligible = /group default/i.test(control.placeholder || '');
    }
    if (!eligible) return;
    var existing = control.parentNode.querySelector('.settings-inheritance-note[data-for="' + control.id + '"]');
    if (!existing) {
      var note = document.createElement('div');
      note.className = 'settings-inheritance-note';
      note.dataset.for = control.id;
      var copy = document.createElement('span');
      copy.className = 'settings-inheritance-copy';
      var reset = document.createElement('button');
      reset.type = 'button';
      reset.textContent = 'Use group default';
      reset.onclick = function() { settingsShellUseInherited(control.id); };
      note.appendChild(copy);
      note.appendChild(reset);
      control.parentNode.insertBefore(note, control.nextSibling);
    }
    _settingsShellUpdateInheritance(control);
  });
}

function _settingsShellRestoreControls(root, values) {
  var all = _settingsShellControls(root);
  all.forEach(function(el, index) {
    var key = _settingsShellControlKey(el, index);
    if (!Object.prototype.hasOwnProperty.call(values || {}, key)) return;
    if (el.type === 'checkbox' || el.type === 'radio') el.checked = !!values[key];
    else el.value = values[key];
    if (el.dataset) {
      delete el.dataset.relayDirty;
      delete el.dataset.statusBarDirty;
    }
  });
}

function settingsShellResetSection(modalOrId) {
  var modal = _settingsShellModal(modalOrId);
  var state = modal && _settingsShellState[modal.id];
  if (!modal || !state || !state.baseline) return;
  var pane = modal.querySelector('.gs-pane.active') || modal;
  var controls = _settingsShellControls(pane);
  controls.forEach(function(el) {
    var all = _settingsShellControls(modal);
    var index = all.indexOf(el);
    var key = _settingsShellControlKey(el, index);
    if (!Object.prototype.hasOwnProperty.call(state.baseline, key)) return;
    if (el.type === 'checkbox' || el.type === 'radio') el.checked = !!state.baseline[key];
    else el.value = state.baseline[key];
  });
  if (modal.id === 'modal-global-settings') settingsAppearancePreview();
  settingsShellMarkDirty(modal);
}

async function settingsShellRequestClose(modalOrId) {
  var modal = _settingsShellModal(modalOrId);
  var state = modal && _settingsShellState[modal.id];
  if (!modal) return;
  if (state && state.dirty && typeof showConfirm === 'function') {
    var discard = await showConfirm('Discard the changes you made in Settings?', {
      title: 'Unsaved changes',
      label: 'Discard changes',
      variant: 'btn-danger',
    });
    if (!discard) return;
  }
  if (state && state.baseline) _settingsShellRestoreControls(modal, state.baseline);
  if (modal.id === 'modal-global-settings') settingsAppearanceRestore();
  _settingsShellApplyDirty(modal, false);
  closeModals();
}

function _settingsShellTabLabel(tab) {
  if (!tab) return 'Settings';
  var label = tab.querySelector && tab.querySelector('span');
  return String(label ? label.textContent : tab.textContent || '').trim();
}

function settingsShellSyncView(modalOrId) {
  var modal = _settingsShellModal(modalOrId);
  if (!modal || !modal.querySelectorAll) return;
  var activeTab = modal.querySelector('.settings-primary-nav .gs-tab.active');
  modal.querySelectorAll('.settings-primary-nav .gs-tab').forEach(function(tab) {
    tab.setAttribute('aria-selected', tab === activeTab ? 'true' : 'false');
    tab.setAttribute('tabindex', tab === activeTab ? '0' : '-1');
  });
  var subtitle = modal.querySelector('.settings-dialog-heading p');
  if (subtitle && activeTab) {
    var detail = activeTab.querySelector('small');
    subtitle.textContent = _settingsShellTabLabel(activeTab)
      + (detail ? ' · ' + detail.textContent.trim() : '');
  }

  var readOnly = false;
  if (modal.id === 'modal-global-settings') {
    var activePane = modal.querySelector('.gs-pane.active');
    var activeSubpane = activePane && activePane.querySelector('.gs-subpane.active');
    readOnly = !!(activePane && activePane.dataset.pane === 'gls-system'
      && activeSubpane && activeSubpane.dataset.subpane === 'gls-daemon');
    var globalSave = document.getElementById('gls-global-save-btn');
    if (globalSave) globalSave.hidden = readOnly;
  }
  if (modal.classList) modal.classList.toggle('settings-read-only', readOnly);
  var dialog = modal.querySelector && modal.querySelector('.settings-dialog');
  if (dialog && dialog.classList) dialog.classList.toggle('settings-read-only', readOnly);
  var stateLabel = _settingsShellStateLabel(modal);
  var state = _settingsShellState[modal.id];
  if (stateLabel) {
    stateLabel.textContent = readOnly
      ? 'Read-only system information'
      : (state && state.dirty ? 'Unsaved changes' : 'No unsaved changes');
  }
}

function _settingsShellSearchIndex(modal) {
  var results = [];
  if (!modal || !modal.querySelectorAll) return results;
  modal.querySelectorAll('.gs-pane').forEach(function(pane) {
    var tabName = pane.dataset ? pane.dataset.pane : '';
    var tab = modal.querySelector('.settings-primary-nav .gs-tab[data-tab="' + tabName + '"]');
    var section = _settingsShellTabLabel(tab);
    pane.querySelectorAll('label, .gs-settings-section-title, .settings-pane-intro h3').forEach(function(node) {
      var text = String(node.textContent || '').replace(/\s+/g, ' ').trim();
      if (!text || text.length > 140) return;
      var subpane = node.closest ? node.closest('.gs-subpane') : null;
      results.push({
        text: text,
        section: section,
        tab: tabName,
        subtab: subpane && subpane.dataset ? subpane.dataset.subpane : '',
        target: node.htmlFor ? document.getElementById(node.htmlFor) : node.querySelector && node.querySelector('input, select, textarea'),
      });
    });
  });
  return results;
}

function _settingsShellOpenSearchResult(modal, item) {
  if (!modal || !item) return;
  if (modal.id === 'modal-group-settings' && typeof switchGsTab === 'function') {
    switchGsTab(item.tab);
    if (item.subtab) {
      var groupSubtab = modal.querySelector('.gs-pane[data-pane="' + item.tab + '"] .gs-subtab[data-subtab="' + item.subtab + '"]');
      if (groupSubtab && typeof switchGsSubTab === 'function') switchGsSubTab(item.tab, groupSubtab);
    }
  } else if (modal.id === 'modal-global-settings' && typeof switchGlsTab === 'function') {
    switchGlsTab(item.tab);
    if (item.subtab) {
      var globalSubtab = modal.querySelector('.gs-pane[data-pane="' + item.tab + '"] .gs-subtab[data-subtab="' + item.subtab + '"]');
      if (globalSubtab && typeof switchGlsSubTab === 'function') switchGlsSubTab(globalSubtab);
    }
  }
  settingsShellSearch(modal.id, '');
  var search = modal.querySelector('input[type="search"]');
  if (search) search.value = '';
  if (item.target && item.target.focus) item.target.focus();
}

function settingsShellSearch(modalOrId, query) {
  var modal = _settingsShellModal(modalOrId);
  var host = modal && modal.querySelector && modal.querySelector('[data-settings-search-results]');
  if (!host) return;
  var normalized = String(query || '').trim().toLowerCase();
  host.innerHTML = '';
  host.hidden = !normalized;
  if (!normalized) return;
  var matches = _settingsShellSearchIndex(modal).filter(function(item) {
    return (item.text + ' ' + item.section).toLowerCase().indexOf(normalized) !== -1;
  }).slice(0, 12);
  if (!matches.length) {
    var empty = document.createElement('div');
    empty.className = 'settings-search-empty';
    empty.textContent = 'No settings found for “' + query + '”';
    host.appendChild(empty);
    return;
  }
  matches.forEach(function(item) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'settings-search-result';
    var title = document.createElement('strong');
    title.textContent = item.text;
    var section = document.createElement('small');
    section.textContent = item.section;
    button.appendChild(title);
    button.appendChild(section);
    button.onclick = function() { _settingsShellOpenSearchResult(modal, item); };
    host.appendChild(button);
  });
}

function _settingsAppearanceDefaults() {
  return {
    contrast: 'balanced',
    accent: 'blue',
    scale: 100,
    terminalFont: 12,
    density: 'compact',
    reduceMotion: false,
  };
}

function _settingsAppearanceRead() {
  var defaults = _settingsAppearanceDefaults();
  try {
    var stored = JSON.parse(localStorage.getItem(_settingsAppearanceKey) || '{}');
    return Object.assign(defaults, stored && typeof stored === 'object' ? stored : {});
  } catch (_) {
    return defaults;
  }
}

function _settingsAppearanceFromControls() {
  var current = _settingsAppearanceRead();
  var contrast = document.getElementById('gls-appearance-contrast');
  var scale = document.getElementById('gls-appearance-scale');
  var terminalFont = document.getElementById('gls-appearance-terminal-font');
  var density = document.getElementById('gls-appearance-density');
  var reduceMotion = document.getElementById('gls-appearance-reduce-motion');
  return {
    contrast: contrast ? contrast.value : current.contrast,
    accent: _settingsAppearanceAccent || current.accent,
    scale: scale ? Number(scale.value) : current.scale,
    terminalFont: terminalFont ? Number(terminalFont.value) : current.terminalFont,
    density: density ? density.value : current.density,
    reduceMotion: reduceMotion ? !!reduceMotion.checked : current.reduceMotion,
  };
}

function _settingsAppearanceApply(value) {
  var root = document.documentElement;
  if (!root || !root.style) return;
  var accents = {
    blue: '#62a8ff',
    violet: '#a78bfa',
    teal: '#2dd4bf',
    amber: '#f0ad39',
  };
  var v = Object.assign(_settingsAppearanceDefaults(), value || {});
  root.dataset.torqueContrast = v.contrast;
  root.dataset.torqueDensity = v.density;
  root.dataset.torqueReduceMotion = v.reduceMotion ? 'true' : 'false';
  root.style.setProperty('--accent', accents[v.accent] || accents.blue);
  root.style.setProperty('--ui-scale', String(v.scale / 100));
  root.style.setProperty('--ui-font-size', String(11 * v.scale / 100) + 'px');
  root.style.setProperty('--terminal-font-size', String(v.terminalFont) + 'px');
  if (typeof _applyEmbeddedTerminalAppearance === 'function') {
    _applyEmbeddedTerminalAppearance();
  }
}

function settingsAppearancePopulate() {
  var value = _settingsAppearanceRead();
  _settingsAppearanceAccent = value.accent;
  var contrast = document.getElementById('gls-appearance-contrast');
  var scale = document.getElementById('gls-appearance-scale');
  var terminalFont = document.getElementById('gls-appearance-terminal-font');
  var density = document.getElementById('gls-appearance-density');
  var reduceMotion = document.getElementById('gls-appearance-reduce-motion');
  if (contrast) contrast.value = value.contrast;
  if (scale) scale.value = value.scale;
  if (terminalFont) terminalFont.value = value.terminalFont;
  if (density) density.value = value.density;
  if (reduceMotion) reduceMotion.checked = !!value.reduceMotion;
  settingsAppearancePreview();
}

function settingsAppearanceSetAccent(name) {
  _settingsAppearanceAccent = name || 'blue';
  settingsAppearancePreview();
  settingsShellMarkDirty('modal-global-settings');
}

function settingsAppearancePreview() {
  var value = _settingsAppearanceFromControls();
  _settingsAppearanceAccent = value.accent;
  _settingsAppearanceApply(value);
  var scaleValue = document.getElementById('gls-appearance-scale-value');
  var terminalValue = document.getElementById('gls-appearance-terminal-font-value');
  if (scaleValue) scaleValue.textContent = value.scale + '%';
  if (terminalValue) terminalValue.textContent = value.terminalFont + 'px';
  document.querySelectorAll('.settings-accent-swatch').forEach(function(button) {
    var active = button.dataset && button.dataset.accent === value.accent;
    button.classList.toggle('active', active);
    button.setAttribute('aria-checked', active ? 'true' : 'false');
  });
}

function settingsAppearanceCommit() {
  var value = _settingsAppearanceFromControls();
  try { localStorage.setItem(_settingsAppearanceKey, JSON.stringify(value)); } catch (_) {}
  _settingsAppearanceApply(value);
}

function settingsAppearanceRestore() {
  _settingsAppearanceApply(_settingsAppearanceRead());
}

// Apply the saved client-local theme before the Settings modal is first opened.
settingsAppearanceRestore();

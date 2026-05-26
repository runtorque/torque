/* User-customizable desktop keybindings.
 *
 * Defaults intentionally live in the browser: the daemon persists only user
 * overrides in GlobalSettings.keybindings.  Bindings use a web-native shape:
 *   { key: string, ctrl: boolean, meta: boolean, alt: boolean, shift: boolean }
 */

var KEYBINDING_ACTIONS = {
  'grid.navigate': {
    order: 10,
    label: 'Grid navigation',
    description: 'Move around the grid, activate the focused item, and remove focused cells.',
    fixed: true,
    display: '\u2191 \u2193 \u2190 \u2192 / Enter / Delete',
    defaultBindings: [
      { id: 'up', label: 'Move up', binding: { key: 'ArrowUp', ctrl: false, meta: false, alt: false, shift: false }, run: function() { moveFocusUp(); } },
      { id: 'down', label: 'Move down', binding: { key: 'ArrowDown', ctrl: false, meta: false, alt: false, shift: false }, run: function() { moveFocusDown(); } },
      { id: 'left', label: 'Move left', binding: { key: 'ArrowLeft', ctrl: false, meta: false, alt: false, shift: false }, run: function() { moveFocusHorizontal(-1); } },
      { id: 'right', label: 'Move right', binding: { key: 'ArrowRight', ctrl: false, meta: false, alt: false, shift: false }, run: function() { moveFocusHorizontal(1); } },
      { id: 'activate', label: 'Activate focused item', binding: { key: 'Enter', ctrl: false, meta: false, alt: false, shift: false }, run: function() { activateFocused(); } },
      { id: 'remove', label: 'Remove focused item', binding: { key: 'Delete', ctrl: false, meta: false, alt: false, shift: false }, run: function() { removeFocused(); } },
      { id: 'remove-backspace', label: 'Remove focused item', binding: { key: 'Backspace', ctrl: false, meta: false, alt: false, shift: false }, run: function() { removeFocused(); } },
    ],
  },
  'group.switch': {
    order: 20,
    label: 'Switch group',
    description: 'Move to the next or previous group.',
    fixed: true,
    display: 'Tab / \u21e7Tab',
    defaultBindings: [
      { id: 'next', label: 'Next group', binding: { key: 'Tab', ctrl: false, meta: false, alt: false, shift: false }, run: function() { switchGroup(1); } },
      { id: 'previous', label: 'Previous group', binding: { key: 'Tab', ctrl: false, meta: false, alt: false, shift: true }, run: function() { switchGroup(-1); } },
    ],
  },
  'composer.focus': {
    order: 30,
    label: 'Focus composer',
    description: 'Focus the selected agent or terminal message composer.',
    defaultBinding: { key: 'c', ctrl: false, meta: false, alt: false, shift: false },
    run: function() {
      return typeof focusComposerForFocusedAgent === 'function'
        ? focusComposerForFocusedAgent()
        : false;
    },
  },
  'terminal.create': {
    order: 40,
    label: 'Create terminal',
    description: 'Add a terminal under the selected agent.',
    defaultBinding: { key: 't', ctrl: false, meta: false, alt: false, shift: false },
    run: function() { openAddTerminalForFocused(); },
  },
  'task.create': {
    order: 50,
    label: 'Create task',
    description: 'Open the board new-task flow.',
    defaultBinding: { key: 'n', ctrl: false, meta: false, alt: false, shift: false },
    run: function() { openAddTaskForFocused(); },
  },
  'panel.toggle': {
    order: 60,
    label: 'Toggle Board panel',
    description: 'Open or close the Board panel.',
    defaultBinding: { key: 'k', ctrl: false, meta: false, alt: false, shift: false },
    run: function() { togglePanel('board'); },
  },
};

var _kbEffectiveCache = null;
var _kbEffectiveCacheFingerprint = '';

function _kbClone(value) {
  if (!value || typeof value !== 'object') return value;
  return JSON.parse(JSON.stringify(value));
}

function _kbActionIds() {
  return Object.keys(KEYBINDING_ACTIONS).sort(function(a, b) {
    var aa = KEYBINDING_ACTIONS[a] || {};
    var bb = KEYBINDING_ACTIONS[b] || {};
    var ao = typeof aa.order === 'number' ? aa.order : 1000;
    var bo = typeof bb.order === 'number' ? bb.order : 1000;
    if (ao !== bo) return ao - bo;
    return a < b ? -1 : (a > b ? 1 : 0);
  });
}

function _kbNormalizeKey(key) {
  var raw = String(key || '');
  if (raw === ' ') return 'Space';
  if (raw === 'Esc') return 'Escape';
  if (raw === 'Del') return 'Delete';
  if (raw === 'Return') return 'Enter';
  if (raw.length === 1) return raw.toLowerCase();
  return raw;
}

function _kbModifierBool(value) {
  return value === true;
}

function normalizeKeybindingDescriptor(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  if (typeof value.key !== 'string' || !value.key) return null;
  return {
    key: _kbNormalizeKey(value.key),
    ctrl: _kbModifierBool(value.ctrl),
    meta: _kbModifierBool(value.meta),
    alt: _kbModifierBool(value.alt),
    shift: _kbModifierBool(value.shift),
  };
}

function keybindingDescriptorFromEvent(e) {
  if (!e) return null;
  var key = _kbNormalizeKey(e.key || '');
  if (!key || key === 'Meta' || key === 'Alt' || key === 'Shift' || key === 'Control') {
    return null;
  }
  return {
    key: key,
    ctrl: !!e.ctrlKey,
    meta: !!e.metaKey,
    alt: !!e.altKey,
    shift: !!e.shiftKey,
  };
}

function _kbBindingFingerprint(binding) {
  var b = normalizeKeybindingDescriptor(binding);
  if (!b) return '';
  return [b.key.toLowerCase(), b.ctrl ? '1' : '0', b.meta ? '1' : '0', b.alt ? '1' : '0', b.shift ? '1' : '0'].join('|');
}

function _kbSameBinding(a, b) {
  return !!_kbBindingFingerprint(a) && _kbBindingFingerprint(a) === _kbBindingFingerprint(b);
}

function _kbMatches(e, binding) {
  var b = normalizeKeybindingDescriptor(binding);
  if (!e || !b) return false;
  if (!!e.ctrlKey !== b.ctrl) return false;
  if (!!e.metaKey !== b.meta) return false;
  if (!!e.altKey !== b.alt) return false;
  if (!!e.shiftKey !== b.shift) return false;
  return _kbNormalizeKey(e.key || '').toLowerCase() === b.key.toLowerCase();
}

function _kbUserOverrides() {
  return (typeof state !== 'undefined'
      && state
      && state.global_settings
      && state.global_settings.keybindings
      && typeof state.global_settings.keybindings === 'object')
    ? state.global_settings.keybindings
    : {};
}

function sanitizeKeybindingOverrides(overrides) {
  var source = overrides && typeof overrides === 'object' ? overrides : {};
  var out = {};
  _kbActionIds().forEach(function(actionId) {
    var action = KEYBINDING_ACTIONS[actionId] || {};
    if (!action.defaultBinding) return;
    var binding = normalizeKeybindingDescriptor(source[actionId]);
    if (binding) out[actionId] = binding;
  });
  return out;
}

function invalidateEffectiveKeybindings() {
  _kbEffectiveCache = null;
  _kbEffectiveCacheFingerprint = '';
}

function _kbSettingsFingerprint() {
  try {
    return JSON.stringify(sanitizeKeybindingOverrides(_kbUserOverrides()));
  } catch (_err) {
    return '';
  }
}

function _kbDefaultBindingForAction(actionId) {
  var action = KEYBINDING_ACTIONS[actionId] || {};
  return normalizeKeybindingDescriptor(action.defaultBinding);
}

function effectiveKeybindings() {
  var fingerprint = _kbSettingsFingerprint();
  if (_kbEffectiveCache && fingerprint === _kbEffectiveCacheFingerprint) {
    return _kbClone(_kbEffectiveCache);
  }
  var overrides = sanitizeKeybindingOverrides(_kbUserOverrides());
  var out = {};
  _kbActionIds().forEach(function(actionId) {
    var action = KEYBINDING_ACTIONS[actionId] || {};
    if (action.defaultBinding) {
      out[actionId] = _kbClone(overrides[actionId] || _kbDefaultBindingForAction(actionId));
    } else if (Array.isArray(action.defaultBindings)) {
      out[actionId] = action.defaultBindings.map(function(item) {
        return _kbClone(item.binding);
      }).filter(Boolean);
    }
  });
  _kbEffectiveCache = _kbClone(out);
  _kbEffectiveCacheFingerprint = fingerprint;
  return out;
}

function keybindingDefaults() {
  var out = {};
  _kbActionIds().forEach(function(actionId) {
    var action = KEYBINDING_ACTIONS[actionId] || {};
    if (action.settingsVisible === false) return;
    out[actionId] = {
      action: actionId,
      order: action.order,
      label: action.label || actionId,
      description: action.description || '',
      fixed: !!action.fixed || !action.defaultBinding,
      display: action.display || '',
      defaultBinding: action.defaultBinding ? _kbClone(normalizeKeybindingDescriptor(action.defaultBinding)) : null,
      defaultBindings: Array.isArray(action.defaultBindings)
        ? action.defaultBindings.map(function(item) { return _kbClone(normalizeKeybindingDescriptor(item.binding)); }).filter(Boolean)
        : [],
    };
  });
  return out;
}

function _kbDisplayKey(key) {
  var normalized = _kbNormalizeKey(key);
  var map = {
    ArrowUp: '\u2191',
    ArrowDown: '\u2193',
    ArrowLeft: '\u2190',
    ArrowRight: '\u2192',
    Enter: 'Enter',
    Backspace: 'Backspace',
    Delete: 'Delete',
    Escape: 'Esc',
    Tab: 'Tab',
    Space: 'Space',
  };
  if (map[normalized]) return map[normalized];
  if (normalized.length === 1) return normalized.toUpperCase();
  return normalized;
}

function kbBindingDisplayName(binding) {
  var b = normalizeKeybindingDescriptor(binding);
  if (!b) return 'Unassigned';
  var mods = [];
  if (b.ctrl) mods.push('\u2303');
  if (b.alt) mods.push('\u2325');
  if (b.shift) mods.push('\u21e7');
  if (b.meta) mods.push('\u2318');
  mods.push(_kbDisplayKey(b.key));
  return mods.join('');
}

function _kbDispatchBindingEntries(actionId) {
  var action = KEYBINDING_ACTIONS[actionId] || {};
  if (Array.isArray(action.defaultBindings) && action.defaultBindings.length) {
    return action.defaultBindings.map(function(item) {
      return {
        actionId: actionId,
        id: item.id || '',
        label: item.label || action.label || actionId,
        binding: normalizeKeybindingDescriptor(item.binding),
        run: item.run || action.run,
        fixed: true,
      };
    }).filter(function(item) { return !!item.binding; });
  }
  var effective = effectiveKeybindings()[actionId];
  var binding = normalizeKeybindingDescriptor(effective || action.defaultBinding);
  if (!binding) return [];
  return [{
    actionId: actionId,
    id: 'default',
    label: action.label || actionId,
    binding: binding,
    run: action.run,
    fixed: false,
  }];
}

function dispatchKeybindingEvent(e) {
  var ids = _kbActionIds();
  for (var i = 0; i < ids.length; i++) {
    var actionId = ids[i];
    var entries = _kbDispatchBindingEntries(actionId);
    for (var j = 0; j < entries.length; j++) {
      var entry = entries[j];
      if (!_kbMatches(e, entry.binding)) continue;
      var result = true;
      if (typeof entry.run === 'function') result = entry.run(e, entry);
      if (result === false) return false;
      if (e && typeof e.preventDefault === 'function') e.preventDefault();
      return true;
    }
  }
  return false;
}

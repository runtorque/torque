/* relay_status.js — status-bar relay-connection indicator (TORQUE:560/:597).
 *
 * CONSUMER of Courier's published relay-connection contract (shared memory
 * 9378aeb9ee33). Renders a compact, self-contained at-a-glance indicator for
 * the daemon<->relay-cloud connection. This is a DIFFERENT connection from the
 * daemon `#conn-dot` (browser<->daemon WS), so the indicator is deliberately
 * visually distinct: a labelled "Relay" dot, never a second bare dot.
 *
 * Data is daemon-global + low-frequency (state-change only). Snapshot read +
 * the `relay_connection` delta op patch `state.relay_connection`; the delta
 * path refreshes ONLY this indicator (+ the modal row if open) and never marks
 * a panel/grid surface (surface-invalidation discipline, CLAUDE.md).
 *
 * Payload shape (do NOT invent; consume the producer's):
 *   { status, enabled, relay_host, daemon_id, last_connected_at, last_error,
 *     retry_count, since }
 * status ∈ connected | connecting | disconnected | error | disabled.
 */

/* Mount point — one-line config. 'taskbar' mounts beside `#taskbar-conn-dot`
 * (user-confirmed placement, TORQUE:560); 'header' mounts beside the daemon
 * `#conn-dot`. The component renders identically regardless of mount. NOTE:
 * the bare `#taskbar-conn-dot` is Tauri-only (CSS gates it behind
 * body.tauri-mode), but the relay indicator is NOT Tauri-gated — it stays
 * browser-visible via the `.relay-status` / `#taskbar .relay-status` rules. */
var RELAY_STATUS_MOUNT = 'taskbar';

/* STUCK-RETRY escalation threshold (architect-endorsed). A connection stuck
 * "connecting"/"disconnected" and retrying forever reads as transient amber
 * indefinitely and UNDER-ALARMS — a stuck retry actually needs operator
 * attention. At/above this many consecutive failed attempts the DOT COLOR
 * escalates amber→red. ~25-30s of stuck retrying at ~5s backoff; tune freely.
 * IMPORTANT: only the dot color escalates — the label/tooltip keep the true
 * status text. */
var RELAY_STUCK_RETRY_THRESHOLD = 5;

var _RELAY_DOT_COLORS = ['green', 'amber', 'red', 'grey'];

/* Built lazily on first render: { root, dot, label }. Kept as direct element
 * references (not re-queried) so updates are cheap and DOM-agnostic. */
var _relayStatusEls = null;

/* Pure view computation — no DOM. Maps the contract payload to a render view.
 * Absent / malformed payload → { visible: false } (pre-producer, or a
 * non-EE/community build with no connector). Distinct from status:"disabled"
 * (connector present but off → grey dot). */
function _relayStatusComputeView(rc) {
  if (!rc || typeof rc !== 'object' || !rc.status) {
    return { visible: false };
  }
  var status = String(rc.status || '');
  var retryCount = Number(rc.retry_count || 0);
  if (!isFinite(retryCount) || retryCount < 0) retryCount = 0;

  var dotColor;
  var statusText;
  switch (status) {
    case 'connected':
      dotColor = 'green';
      statusText = 'connected';
      break;
    case 'connecting':
      // First connect / reconnecting (backoff active, not yet attached).
      dotColor = 'amber';
      statusText = 'connecting';
      break;
    case 'disconnected':
      // Was connected, dropped, retrying — transient (Courier semantic).
      dotColor = 'amber';
      statusText = 'disconnected';
      break;
    case 'error':
      // Persistent failure (auth/TLS/config) with last_error.
      dotColor = 'red';
      statusText = 'error';
      break;
    case 'disabled':
      // Connector not enabled/configured — off, no error.
      dotColor = 'grey';
      statusText = 'disabled';
      break;
    default:
      // Unknown status from a newer producer — render generically but keep
      // the raw text so operators still see something meaningful.
      dotColor = 'amber';
      statusText = status;
      break;
  }

  // STUCK-RETRY escalation: only the dot color changes amber→red; the
  // label/tooltip keep the true status text (still connecting/disconnected).
  var escalated = false;
  if ((status === 'connecting' || status === 'disconnected')
      && retryCount >= RELAY_STUCK_RETRY_THRESHOLD) {
    dotColor = 'red';
    escalated = true;
  }

  return {
    visible: true,
    status: status,
    statusText: statusText,
    dotColor: dotColor,
    escalated: escalated,
    label: 'Relay',
    tooltip: _relayStatusTooltip(rc, statusText, retryCount),
  };
}

/* Full-detail tooltip. The at-a-glance dot/label stay generic; all five
 * states stay distinct in the tooltip text. */
function _relayStatusTooltip(rc, statusText, retryCount) {
  var lines = ['Relay: ' + statusText];
  var host = String((rc && rc.relay_host) || '');
  if (host) lines.push('Host: ' + host);
  var lastConnected = String((rc && rc.last_connected_at) || '');
  if (lastConnected) lines.push('Last connected: ' + lastConnected);
  if (retryCount > 0) {
    lines.push('Retrying, ' + retryCount
      + (retryCount === 1 ? ' attempt' : ' attempts'));
  }
  var since = String((rc && rc.since) || '');
  if (since) lines.push('Since: ' + since);
  var lastError = String((rc && rc.last_error) || '');
  if (lastError) lines.push('Error: ' + lastError);
  return lines.join('\n');
}

function _relayStatusBuildEls() {
  if (typeof document === 'undefined'
      || typeof document.createElement !== 'function') {
    return null;
  }
  var root = document.createElement('div');
  root.id = 'relay-status-indicator';
  root.className = 'relay-status';
  var dot = document.createElement('span');
  dot.className = 'relay-status-dot';
  dot.setAttribute('aria-hidden', 'true');
  var label = document.createElement('span');
  label.className = 'relay-status-label';
  label.textContent = 'Relay';
  root.appendChild(dot);
  root.appendChild(label);
  return { root: root, dot: dot, label: label };
}

/* Mount the indicator beside the configured connection dot (after it, so the
 * two connection indicators sit together but stay distinct). Idempotent. */
function _relayStatusEnsureMounted() {
  if (_relayStatusEls && _relayStatusEls.root) return _relayStatusEls;
  if (typeof document === 'undefined') return null;
  var built = _relayStatusBuildEls();
  if (!built) return null;
  var anchorId = RELAY_STATUS_MOUNT === 'taskbar'
    ? 'taskbar-conn-dot'
    : 'conn-dot';
  var anchor = document.getElementById ? document.getElementById(anchorId) : null;
  if (anchor && anchor.parentNode
      && typeof anchor.parentNode.insertBefore === 'function') {
    anchor.parentNode.insertBefore(built.root, anchor.nextElementSibling || null);
  } else if (document.body && typeof document.body.appendChild === 'function') {
    document.body.appendChild(built.root);
  } else {
    return null;
  }
  _relayStatusEls = built;
  return built;
}

function _relayStatusApplyDot(dot, view) {
  if (!dot || !dot.classList) return;
  for (var i = 0; i < _RELAY_DOT_COLORS.length; i++) {
    dot.classList.remove('relay-status-dot--' + _RELAY_DOT_COLORS[i]);
  }
  dot.classList.add('relay-status-dot--' + view.dotColor);
  if (view.escalated) dot.classList.add('relay-status-dot--escalated');
  else dot.classList.remove('relay-status-dot--escalated');
}

/* Primary entry point: refresh the at-a-glance indicator (+ modal row if
 * present) from `state.relay_connection`. Cheap, targeted DOM update — safe
 * to call directly on the low-frequency `relay_connection` delta op. */
function refreshRelayStatusIndicator() {
  var rc = (typeof state !== 'undefined' && state) ? state.relay_connection : null;
  var view = _relayStatusComputeView(rc);

  if (!view.visible) {
    if (_relayStatusEls && _relayStatusEls.root) {
      _relayStatusEls.root.hidden = true;
      if (_relayStatusEls.root.classList) {
        _relayStatusEls.root.classList.add('relay-status--hidden');
      }
    }
    _relayStatusRenderModalRow(view, rc);
    return;
  }

  var els = _relayStatusEnsureMounted();
  if (els && els.root) {
    els.root.hidden = false;
    if (els.root.classList) els.root.classList.remove('relay-status--hidden');
    els.root.title = view.tooltip;
    if (typeof els.root.setAttribute === 'function') {
      els.root.setAttribute('data-relay-status', view.status);
    }
    _relayStatusApplyDot(els.dot, view);
    if (els.label) els.label.textContent = view.label;
  }
  _relayStatusRenderModalRow(view, rc);
}

function _relayStatusSetModalText(id, value) {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var el = document.getElementById(id);
  if (!el) return;
  el.textContent = (value === null || value === undefined || value === '')
    ? '—'
    : String(value);
}

/* Whether each Relay sub-surface currently has data to show. The section
 * wrapper is visible if EITHER is present so config can render even on a build
 * that publishes `relay_config` without a `relay_connection` signal (and vice
 * versa). Module-level so the two independent renderers coordinate one
 * `section.hidden`. */
var _relayConnModalVisible = false;
var _relayConfigModalVisible = false;

/* Single owner of `#gls-relay-section`.hidden — the OR of the two sub-blocks. */
function _relaySectionUpdateVisibility() {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var section = document.getElementById('gls-relay-section');
  if (!section) return;
  section.hidden = !(_relayConnModalVisible || _relayConfigModalVisible);
}

/* Connection sub-block of the daemon-status modal "Relay" section. Driven from
 * `state.relay_connection`; absent field → hide the connection block (the
 * section may still show if `relay_config` is present). */
function _relayStatusRenderModalRow(view, rc) {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var section = document.getElementById('gls-relay-section');
  if (!section) return;
  if (rc === undefined) {
    rc = (typeof state !== 'undefined' && state) ? state.relay_connection : null;
  }
  if (!view) view = _relayStatusComputeView(rc);

  var block = document.getElementById('gls-relay-connection-block');
  _relayConnModalVisible = !!view.visible;
  if (!view.visible) {
    if (block) block.hidden = true;
    _relaySectionUpdateVisibility();
    return;
  }
  if (block) block.hidden = false;
  _relaySectionUpdateVisibility();
  _relayStatusApplyDot(document.getElementById('gls-relay-status-dot'), view);
  _relayStatusSetModalText('gls-relay-status-text', view.statusText);
  _relayStatusSetModalText('gls-relay-host', rc && rc.relay_host);
  _relayStatusSetModalText('gls-relay-retry-count',
    rc ? String(Number(rc.retry_count || 0)) : '0');
  _relayStatusSetModalText('gls-relay-since', rc && rc.since);
  _relayStatusSetModalText('gls-relay-last-error', rc && rc.last_error);
}

/* ----------------------------------------------------------------------------
 * Relay CONFIG + provenance (TORQUE:603 #1).
 *
 * CONSUMER of Courier's resolved-config-with-provenance contract (shared memory
 * 40c1c73e6bec). Shape (do NOT invent; consume the producer's `relay_config`):
 *   { config:  { enabled, relay_url, daemon_id, credential_id, private_key_path },
 *     sources: { <field>: { value, source } } }
 * source ∈ "settings" | "ee_connector.json" | "env" | "" (unset);
 * precedence settings > ee_connector.json > env. private_key_path is BY PATH
 * only — inline-PEM resolves to source "ee_connector.json" with value "" and
 * NEVER leaks the PEM here.
 *
 * Read from 3 same-shape surfaces: the snapshot `state.relay_config`, the
 * `get_global_settings` response `relay_config`, and the low-frequency
 * `relay_config` delta op. Like the connection signal, the delta patches state
 * in place + refreshes ONLY this section — never a panel/grid surface.
 * -------------------------------------------------------------------------- */

/* Field order + metadata. `key` matches the contract `config`/`sources` keys;
 * the editable settings-layer save key is `relay_<key>` (relay_enabled,
 * relay_url, relay_daemon_id, relay_credential_id, relay_private_key_path). */
var RELAY_CONFIG_FIELDS = [
  { key: 'enabled',          type: 'bool', inputId: 'gls-relay-enabled' },
  { key: 'relay_url',        type: 'text', inputId: 'gls-relay-url' },
  { key: 'daemon_id',        type: 'text', inputId: 'gls-relay-daemon-id' },
  { key: 'credential_id',    type: 'text', inputId: 'gls-relay-credential-id' },
  { key: 'private_key_path', type: 'text', inputId: 'gls-relay-private-key-path' },
];

function _relaySourceLabel(source) {
  switch (source) {
    case 'settings': return 'settings';
    case 'ee_connector.json': return 'ee_connector.json';
    case 'env': return 'env';
    default: return 'unset';
  }
}

function _relaySourceClass(source) {
  switch (source) {
    case 'settings': return 'settings';
    case 'ee_connector.json': return 'file';
    case 'env': return 'env';
    default: return 'unset';
  }
}

/* Pure view computation — no DOM. Maps the `relay_config` payload to a per-field
 * view. Absent / malformed → { visible: false } (pre-producer / community).
 *
 * Per field:
 *   - source / sourceLabel / sourceClass: provenance badge.
 *   - For text: `textValue` is the editable SETTINGS-LAYER override — non-empty
 *     only when the effective source is "settings" (so saving an untouched
 *     inherited field re-sends "" and keeps the file/env fallback). When the
 *     value is inherited, the effective value surfaces as `placeholder` so the
 *     operator still sees what is in effect — for private_key_path this is "" on
 *     inline-PEM (never the PEM) and a path otherwise.
 *   - For bool: `checked` is the effective value; the badge carries provenance.
 */
function _relayConfigComputeView(rc) {
  if (!rc || typeof rc !== 'object') return { visible: false, fields: [] };
  var sources = (rc.sources && typeof rc.sources === 'object') ? rc.sources : {};
  var config = (rc.config && typeof rc.config === 'object') ? rc.config : {};
  var fields = RELAY_CONFIG_FIELDS.map(function(f) {
    var src = (sources[f.key] && typeof sources[f.key] === 'object') ? sources[f.key] : {};
    var source = String(src.source || '');
    var fromSettings = source === 'settings';
    var effective;
    if (Object.prototype.hasOwnProperty.call(config, f.key)) effective = config[f.key];
    else if (Object.prototype.hasOwnProperty.call(src, 'value')) effective = src.value;
    else effective = (f.type === 'bool' ? false : '');
    var view = {
      key: f.key,
      type: f.type,
      inputId: f.inputId,
      source: source,
      sourceLabel: _relaySourceLabel(source),
      sourceClass: _relaySourceClass(source),
    };
    if (f.type === 'bool') {
      view.checked = !!effective;
    } else {
      var effStr = (effective === null || effective === undefined) ? '' : String(effective);
      var srcStr = (src.value === null || src.value === undefined) ? '' : String(src.value);
      view.textValue = fromSettings ? srcStr : '';
      view.placeholder = fromSettings ? '' : effStr;
    }
    return view;
  });
  return { visible: true, fields: fields };
}

/* Skip clobbering an input the operator is actively editing (focused) or has an
 * unsaved draft in (dirty) — a low-frequency `relay_config` delta must never
 * wipe an in-progress edit. */
function _relayConfigInputLocked(el) {
  if (!el) return false;
  if (typeof document !== 'undefined' && document.activeElement === el) return true;
  if (el.dataset && el.dataset.relayDirty === '1') return true;
  return false;
}

function _relayConfigApplyBadge(id, view) {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var badge = document.getElementById(id);
  if (!badge) return;
  badge.textContent = view.sourceLabel;
  if (badge.classList) {
    ['settings', 'file', 'env', 'unset'].forEach(function(c) {
      badge.classList.remove('relay-source-badge--' + c);
    });
    badge.classList.add('relay-source-badge--' + view.sourceClass);
  }
  if (typeof badge.setAttribute === 'function') {
    badge.setAttribute('title', 'Effective source: ' + view.sourceLabel);
  }
}

/* DOM apply for the config sub-block. opts.force=true (modal open) repopulates
 * every input and clears dirty flags; otherwise (delta / snapshot) it preserves
 * focused/dirty inputs so a delta never clobbers an in-progress edit. Badges
 * (read-only provenance) always refresh. */
function refreshRelayConfigModal(opts) {
  if (typeof document === 'undefined' || !document.getElementById) return;
  opts = opts || {};
  var block = document.getElementById('gls-relay-config-block');
  var rc = (typeof state !== 'undefined' && state) ? state.relay_config : null;
  var view = _relayConfigComputeView(rc);

  _relayConfigModalVisible = !!view.visible;
  if (block) block.hidden = !view.visible;
  _relaySectionUpdateVisibility();
  if (!view.visible) return;

  for (var i = 0; i < view.fields.length; i++) {
    var f = view.fields[i];
    var input = document.getElementById(f.inputId);
    if (input && (opts.force || !_relayConfigInputLocked(input))) {
      if (f.type === 'bool') {
        input.checked = f.checked;
      } else {
        input.value = f.textValue;
        // Surface a non-empty inherited effective value as the placeholder;
        // keep the static HTML hint when there is nothing effective (e.g.
        // private_key_path on inline-PEM, whose effective value is "").
        if (f.placeholder) input.placeholder = f.placeholder;
      }
      if (opts.force && input.dataset) delete input.dataset.relayDirty;
    }
    _relayConfigApplyBadge(f.inputId + '-badge', f);
  }
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    _relayStatusComputeView: _relayStatusComputeView,
    _relayConfigComputeView: _relayConfigComputeView,
    RELAY_STUCK_RETRY_THRESHOLD: RELAY_STUCK_RETRY_THRESHOLD,
  };
}

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
 * `#conn-dot`. The component renders identically regardless of mount. The
 * labelled taskbar daemon indicator and this relay indicator are both
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
  var insertAfter = anchor;
  // The taskbar daemon indicator wraps the historical #taskbar-conn-dot with
  // its "Daemon" label. Keep anchoring on the dot id, but insert the relay
  // indicator after the whole labelled daemon member so the two indicators sit
  // together as distinct siblings.
  if (anchor && anchor.parentNode
      && anchor.parentNode.id === 'daemon-status-indicator') {
    insertAfter = anchor.parentNode;
  }
  if (insertAfter && insertAfter.parentNode
      && typeof insertAfter.parentNode.insertBefore === 'function') {
    insertAfter.parentNode.insertBefore(built.root, insertAfter.nextElementSibling || null);
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
  var relaySubtab = document.querySelector
    ? document.querySelector('#modal-global-settings .gs-subtab[data-subtab="gls-relay"]')
    : null;
  if (relaySubtab) relaySubtab.hidden = section.hidden;
  if (section.hidden && relaySubtab && relaySubtab.classList
      && relaySubtab.classList.contains('active')) {
    var daemonSubtab = document.querySelector
      ? document.querySelector('#modal-global-settings .gs-subtab[data-subtab="gls-daemon"]')
      : null;
    if (daemonSubtab && typeof switchGlsSubTab === 'function') {
      switchGlsSubTab(daemonSubtab);
    } else {
      relaySubtab.classList.remove('active');
      var relayPane = document.querySelector
        ? document.querySelector('#modal-global-settings .gs-subpane[data-subpane="gls-relay"]')
        : null;
      var daemonPane = document.querySelector
        ? document.querySelector('#modal-global-settings .gs-subpane[data-subpane="gls-daemon"]')
        : null;
      if (daemonSubtab && daemonSubtab.classList) daemonSubtab.classList.add('active');
      if (relayPane && relayPane.classList) relayPane.classList.remove('active');
      if (daemonPane && daemonPane.classList) daemonPane.classList.add('active');
    }
  }
  // The Test-connectivity probe slot (TORQUE:603 #2) rides the section
  // visibility: show the button whenever the Relay section shows. This toggles
  // ONLY `hidden` — it must never clear the slot's contents, so a low-frequency
  // relay_config / relay_connection delta refresh (which lands here via the
  // sub-block renderers) can't wipe the last probe result or reset the button.
  // The probe result/button are owned solely by the probe handler below.
  var slot = document.getElementById('gls-relay-probe-slot');
  if (slot) slot.hidden = section.hidden;
  // Daemon-credential provisioning (:676 client half) also rides the Relay
  // section. The pasted pairing token is intentionally not stored in state; the
  // control is reset only on success/modal close, while ordinary config deltas
  // update button gating without touching the token input.
  var credSlot = document.getElementById('gls-relay-daemon-credential-slot');
  if (credSlot) credSlot.hidden = section.hidden;
  relayDaemonCredentialRefreshButtonState();
  // The device-link slot (TORQUE:603 #3) likewise rides the section visibility.
  // _relayDeviceLinkRefreshButtonState() then flips ONLY the generate button's
  // enabled state from the gate; the displayed secret is left untouched and
  // survives the delta (engineer decision 43ab33a09a84). A low-frequency
  // relay_config / relay_connection delta refresh (which lands here via the
  // sub-block renderers) can thus update the button without disturbing a live
  // minted secret. The secret is owned solely by the device-link handler below
  // and is cleared only on Dismiss and modal close.
  var dlSlot = document.getElementById('gls-relay-device-link-slot');
  if (dlSlot) dlSlot.hidden = section.hidden;
  _relayDeviceLinkRefreshButtonState();
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
 * Relay CONFIG (TORQUE:603 #1).
 *
 * CONSUMER of Courier's resolved config contract (shared memory
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

/* Pure view computation — no DOM. Maps the `relay_config` payload to a per-field
 * view. Absent / malformed → { visible: false } (pre-producer / community).
 *
 * Per field:
 *   - source: config source used only to decide whether an editable text input
 *     should hold a settings-layer override or inherit from file/env.
 *   - For text: `textValue` is the editable SETTINGS-LAYER override — non-empty
 *     only when the effective source is "settings" (so saving an untouched
 *     inherited field re-sends "" and keeps the file/env fallback). When the
 *     value is inherited, the effective value surfaces as `placeholder` so the
 *     operator still sees what is in effect — for private_key_path this is "" on
 *     inline-PEM (never the PEM) and a path otherwise.
 *   - For bool: `checked` is the effective value.
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

/* DOM apply for the config sub-block. opts.force=true (modal open) repopulates
 * every input and clears dirty flags; otherwise (delta / snapshot) it preserves
 * focused/dirty inputs so a delta never clobbers an in-progress edit. */
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
  }
}

/* ----------------------------------------------------------------------------
 * Relay TEST-CONNECTIVITY probe (TORQUE:603 #2).
 *
 * CONSUMER of Courier's merged daemon-side probe (server cmd
 * `test_relay_connection` → `cloud_hooks.probe_relay_connection`), which returns
 * a structured, ACTIONABLE result:
 *   { type: "relay_test_result", status, message, detail }
 * status ∈ ok | reachable_unauthed | ca_missing | unreachable | auth_rejected |
 *          disabled | misconfigured  (RELAY_PROBE_* on the daemon side).
 * `message` is human-readable actionable guidance; `detail` is extra context.
 *
 * The probe is a one-shot command RESPONSE, NOT a delta/surface: the button
 * sends the command, the daemon replies once, and the result renders into the
 * reserved `#gls-relay-probe-slot` (mounted by :603 #1). The slot's content is
 * owned here and is preserved across relay_config / relay_connection delta
 * refreshes (those never touch the slot).
 * -------------------------------------------------------------------------- */

/* status → dot color group, mirroring the relay-status dot grouping: ok green,
 * reachable_unauthed amber, disabled grey, everything else (config/TLS/auth
 * failures the operator must act on) red. */
var RELAY_PROBE_STATUS_COLORS = {
  ok: 'green',
  reachable_unauthed: 'amber',
  disabled: 'grey',
  ca_missing: 'red',
  unreachable: 'red',
  auth_rejected: 'red',
  misconfigured: 'red',
};

/* Only the success state carries a glyph; the colored dot conveys the rest. */
var RELAY_PROBE_STATUS_ICONS = {
  ok: '✓',
};

var _relayProbeInFlight = false;

/* Self-contained HTML escaper (matches render.js's esc()). Kept local rather
 * than delegating to the global esc() because relay_status.js loads BEFORE
 * render.js in webview.html — this keeps the probe renderer XSS-safe with no
 * load-order dependency. All server-supplied message/detail text flows through
 * here before reaching innerHTML. */
function _relayProbeEsc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* Pure view computation — no DOM. Maps a relay_test_result payload to a render
 * view. Absent / malformed (no status) → { visible: false }. */
function _relayProbeComputeView(result) {
  if (!result || typeof result !== 'object' || !result.status) {
    return { visible: false };
  }
  var status = String(result.status || '');
  return {
    visible: true,
    status: status,
    // Unknown status from a newer producer → amber (render something useful).
    dotColor: RELAY_PROBE_STATUS_COLORS[status] || 'amber',
    icon: RELAY_PROBE_STATUS_ICONS[status] || '',
    message: String(result.message || ''),
    detail: String(result.detail || ''),
  };
}

/* Render the actionable result line into `#gls-relay-probe-result`. All
 * server-supplied text is esc()'d. Rebuilds innerHTML so stale dot-color
 * classes never linger between probes. */
function _relayProbeRenderResult(view) {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var el = document.getElementById('gls-relay-probe-result');
  if (!el) return;
  if (!view || !view.visible) {
    el.hidden = true;
    el.innerHTML = '';
    if (typeof el.removeAttribute === 'function') {
      el.removeAttribute('data-relay-probe-status');
    }
    return;
  }
  var html = '<span class="relay-status-dot relay-status-dot--'
    + _relayProbeEsc(view.dotColor) + '" aria-hidden="true"></span>';
  if (view.icon) {
    html += '<span class="gls-relay-probe-icon" aria-hidden="true">'
      + _relayProbeEsc(view.icon) + '</span>';
  }
  html += '<span class="gls-relay-probe-message">'
    + _relayProbeEsc(view.message) + '</span>';
  if (view.detail) {
    html += '<span class="gls-relay-probe-detail">'
      + _relayProbeEsc(view.detail) + '</span>';
  }
  el.hidden = false;
  if (typeof el.setAttribute === 'function') {
    el.setAttribute('data-relay-probe-status', view.status);
  }
  el.innerHTML = html;
}

/* Toggle the in-flight loading state: disable the button + "Testing…" label,
 * and show a transient loading line. */
function _relayProbeSetInFlight(inFlight) {
  _relayProbeInFlight = !!inFlight;
  if (typeof document === 'undefined' || !document.getElementById) return;
  var btn = document.getElementById('gls-relay-probe-test');
  if (btn) {
    btn.disabled = !!inFlight;
    btn.textContent = inFlight ? 'Testing…' : 'Test connectivity';
  }
  if (inFlight) {
    var el = document.getElementById('gls-relay-probe-result');
    if (el) {
      el.hidden = false;
      if (typeof el.removeAttribute === 'function') {
        el.removeAttribute('data-relay-probe-status');
      }
      el.innerHTML = '<span class="gls-relay-probe-message">'
        + 'Testing relay connectivity…</span>';
    }
  }
}

/* Button onclick: send the daemon probe command (mirrors the established
 * command transport, e.g. board-sync's preflight). The probe takes up to ~10s
 * server-side, hence the disabled/loading state. Guard against double-sends. */
function testRelayConnection() {
  if (_relayProbeInFlight) return;
  _relayProbeSetInFlight(true);
  if (typeof send === 'function') {
    send({ cmd: 'test_relay_connection' });
  }
}

/* Handle the daemon's one-shot `relay_test_result` response: clear the loading
 * state and render the actionable result. */
function handleRelayTestResult(msg) {
  if (!msg || msg.type !== 'relay_test_result') return;
  _relayProbeSetInFlight(false);
  _relayProbeRenderResult(_relayProbeComputeView(msg));
}

/* ----------------------------------------------------------------------------
 * Relay DAEMON CREDENTIAL provisioning (:676 client half).
 *
 * v1 flow: client generates an ES256 daemon key locally, operator pastes a
 * one-time `pairing_token` minted OUT-OF-BAND by a relay admin
 * (/v1/admin/pairing-tokens), daemon posts the PUBLIC JWK to /v1/pair, then
 * stores ONLY {relay_credential_id, relay_private_key_path} in Global Settings.
 *
 * FUTURE follow-up (not v1): admin-authenticated in-app pairing-token minting.
 * This client UI deliberately never calls /v1/admin/pairing-tokens.
 * -------------------------------------------------------------------------- */

var _relayDaemonCredentialInFlight = false;
var _relayDaemonCredentialConfirming = false;

function _relayConfigEffectiveValue(rc, key) {
  if (!rc || typeof rc !== 'object') return '';
  var config = (rc.config && typeof rc.config === 'object') ? rc.config : {};
  if (Object.prototype.hasOwnProperty.call(config, key)) {
    return config[key];
  }
  var sources = (rc.sources && typeof rc.sources === 'object') ? rc.sources : {};
  var src = (sources[key] && typeof sources[key] === 'object') ? sources[key] : {};
  return Object.prototype.hasOwnProperty.call(src, 'value') ? src.value : '';
}

function _relayDaemonCredentialComputeGate(rc) {
  if (!rc || typeof rc !== 'object') {
    return { visible: false, canGenerate: false, reason: '' };
  }
  var relayUrl = String(_relayConfigEffectiveValue(rc, 'relay_url') || '').trim();
  var daemonId = String(_relayConfigEffectiveValue(rc, 'daemon_id') || '').trim();
  if (!relayUrl && !daemonId) {
    return { visible: true, canGenerate: false,
      reason: 'Set the relay URL and daemon ID before generating a daemon credential.' };
  }
  if (!relayUrl) {
    return { visible: true, canGenerate: false,
      reason: 'Set the relay URL before generating a daemon credential.' };
  }
  if (!daemonId) {
    return { visible: true, canGenerate: false,
      reason: 'Set the daemon ID before generating a daemon credential.' };
  }
  return { visible: true, canGenerate: true, reason: '' };
}

function _relayDaemonCredentialTokenValue() {
  if (typeof document === 'undefined' || !document.getElementById) return '';
  var input = document.getElementById('gls-relay-daemon-credential-token');
  return input ? String(input.value || '').trim() : '';
}

function _relayDaemonCredentialCurrentCredentialId(rc) {
  return String(_relayConfigEffectiveValue(rc, 'credential_id') || '').trim();
}

function _relayDaemonCredentialClearError() {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var el = document.getElementById('gls-relay-daemon-credential-error');
  if (el) { el.hidden = true; el.innerHTML = ''; }
}

function _relayDaemonCredentialClearResult() {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var el = document.getElementById('gls-relay-daemon-credential-result');
  if (el) { el.hidden = true; el.innerHTML = ''; }
}

function _relayDaemonCredentialClearRecovery() {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var el = document.getElementById('gls-relay-daemon-credential-recovery');
  if (el) { el.hidden = true; el.innerHTML = ''; }
}

function _relayDaemonCredentialSetInFlight(on) {
  _relayDaemonCredentialInFlight = !!on;
  if (typeof document === 'undefined' || !document.getElementById) return;
  var btn = document.getElementById('gls-relay-daemon-credential-generate');
  if (btn) {
    btn.textContent = on ? 'Generating…' : 'Generate';
  }
  relayDaemonCredentialRefreshButtonState();
}

function _relayDaemonCredentialSetConfirming(on, credentialId) {
  _relayDaemonCredentialConfirming = !!on;
  if (typeof document === 'undefined' || !document.getElementById) return;
  var box = document.getElementById('gls-relay-daemon-credential-confirm');
  if (box) box.hidden = !on;
  var msg = document.getElementById('gls-relay-daemon-credential-confirm-msg');
  if (msg) {
    msg.textContent = on
      ? 'This daemon already has a relay credential (' + String(credentialId || '')
        + '). Re-generating registers a NEW credential and makes it active on the relay, '
        + "which can disrupt this daemon's current live connection. Generate a new "
        + 'credential anyway?'
      : '';
  }
  var btn = document.getElementById('gls-relay-daemon-credential-generate');
  if (btn) btn.hidden = !!on;
}

function relayDaemonCredentialRefreshButtonState() {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var rc = (typeof state !== 'undefined' && state) ? state.relay_config : null;
  var gate = _relayDaemonCredentialComputeGate(rc);
  var tokenPresent = !!_relayDaemonCredentialTokenValue();
  var btn = document.getElementById('gls-relay-daemon-credential-generate');
  if (btn) {
    btn.hidden = !!_relayDaemonCredentialConfirming;
    btn.disabled = _relayDaemonCredentialInFlight || !gate.canGenerate || !tokenPresent;
    if (typeof btn.setAttribute === 'function') {
      var title = gate.canGenerate
        ? (tokenPresent ? 'Generate a daemon credential from the pasted one-time token'
          : 'Paste a one-time pairing token first')
        : gate.reason;
      btn.setAttribute('title', title);
    }
  }
  var hint = document.getElementById('gls-relay-daemon-credential-hint');
  if (hint) {
    hint.hidden = !!gate.canGenerate;
    hint.textContent = gate.canGenerate ? '' : gate.reason;
  }
}

function _relayDaemonCredentialRenderError(view) {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var el = document.getElementById('gls-relay-daemon-credential-error');
  if (!el) return;
  var html = '<span class="gls-relay-daemon-credential-error-msg">'
    + _relayProbeEsc(view.message) + '</span>';
  if (view.detail) {
    html += '<span class="gls-relay-daemon-credential-error-detail">'
      + _relayProbeEsc(view.detail) + '</span>';
  }
  el.hidden = false;
  el.innerHTML = html;
}

function _relayDaemonCredentialComputeView(msg) {
  if (!msg || typeof msg !== 'object' || msg.type !== 'daemon_credential') {
    return { kind: 'none' };
  }
  if (msg.ok === true) {
    return {
      kind: 'success',
      credentialId: String(msg.credential_id || ''),
      daemonId: String(msg.daemon_id || ''),
      ownerUserId: String(msg.owner_user_id || ''),
      privateKeyPath: String(msg.private_key_path || ''),
      provenance: (msg.provenance && typeof msg.provenance === 'object')
        ? msg.provenance : {},
    };
  }
  if (msg.error === 'settings_write_failed' || msg.recoverable === true) {
    return {
      kind: 'recovery',
      message: String(msg.message || (
        "Relay accepted the credential, but Torque couldn't save it to Settings."
      )),
      credentialId: String(msg.credential_id || ''),
      privateKeyPath: String(msg.private_key_path || ''),
      detail: String(msg.detail || ''),
    };
  }
  return {
    kind: 'error',
    error: String(msg.error || ''),
    message: String(msg.message || 'Could not generate a daemon credential.'),
    detail: String(msg.detail || ''),
  };
}

function _relayDaemonCredentialRenderSuccess(view) {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var el = document.getElementById('gls-relay-daemon-credential-result');
  if (!el) return;
  var provenanceLabel = 'stored in Settings';
  if (view.provenance && view.provenance.private_key_path === 'local_keygen') {
    provenanceLabel = 'local keygen → Settings';
  }
  var html = '<div class="gls-relay-daemon-credential-success-line">'
    + '<span class="gls-relay-daemon-credential-badge">'
    + _relayProbeEsc(provenanceLabel) + '</span>'
    + '<span>Daemon credential generated. Settings now contain '
    + '<code>relay_credential_id</code> and <code>relay_private_key_path</code>.</span>'
    + '</div>';
  html += '<div class="gls-relay-daemon-credential-field">'
    + '<span>Credential ID</span><code>'
    + _relayProbeEsc(view.credentialId) + '</code></div>';
  html += '<div class="gls-relay-daemon-credential-field">'
    + '<span>Private key path</span><code>'
    + _relayProbeEsc(view.privateKeyPath) + '</code></div>';
  if (view.ownerUserId) {
    html += '<div class="gls-relay-daemon-credential-field">'
      + '<span>Owner user</span><code>'
      + _relayProbeEsc(view.ownerUserId) + '</code></div>';
  }
  el.hidden = false;
  el.innerHTML = html;
}

function _relayDaemonCredentialRenderRecovery(view) {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var el = document.getElementById('gls-relay-daemon-credential-recovery');
  if (!el) return;
  var html = '<div class="gls-relay-daemon-credential-recovery-line">'
    + '<span class="gls-relay-daemon-credential-badge gls-relay-daemon-credential-badge--warning">'
    + 'Recovery</span>'
    + '<span>' + _relayProbeEsc(view.message) + '</span>'
    + '</div>';
  html += '<div class="gls-relay-daemon-credential-field">'
    + '<span>Credential ID</span><code>'
    + _relayProbeEsc(view.credentialId) + '</code></div>';
  html += '<div class="gls-relay-daemon-credential-field">'
    + '<span>Private key path</span><code>'
    + _relayProbeEsc(view.privateKeyPath) + '</code></div>';
  if (view.detail) {
    html += '<div class="gls-relay-daemon-credential-recovery-detail">'
      + _relayProbeEsc(view.detail) + '</div>';
  }
  el.hidden = false;
  el.innerHTML = html;
}

function _relayDaemonCredentialApplyRelayConfig(msg) {
  if (!msg || !msg.relay_config || typeof msg.relay_config !== 'object') return;
  if (typeof state !== 'undefined' && state) {
    state.relay_config = msg.relay_config;
  }
  if (typeof refreshRelayConfigModal === 'function') {
    refreshRelayConfigModal({ force: true });
  }
}

function _relayDaemonCredentialReset() {
  _relayDaemonCredentialInFlight = false;
  _relayDaemonCredentialSetConfirming(false);
  if (typeof document !== 'undefined' && document.getElementById) {
    var token = document.getElementById('gls-relay-daemon-credential-token');
    if (token) token.value = '';
    var btn = document.getElementById('gls-relay-daemon-credential-generate');
    if (btn) btn.textContent = 'Generate';
  }
  _relayDaemonCredentialClearError();
  _relayDaemonCredentialClearResult();
  _relayDaemonCredentialClearRecovery();
  relayDaemonCredentialRefreshButtonState();
}

function _relayDaemonCredentialSubmit(skipExistingCredentialGuard) {
  if (_relayDaemonCredentialInFlight) return;
  var rc = (typeof state !== 'undefined' && state) ? state.relay_config : null;
  var gate = _relayDaemonCredentialComputeGate(rc);
  var token = _relayDaemonCredentialTokenValue();
  _relayDaemonCredentialClearError();
  _relayDaemonCredentialClearResult();
  _relayDaemonCredentialClearRecovery();
  if (!gate.canGenerate) {
    _relayDaemonCredentialRenderError({ message: gate.reason, detail: '' });
    return;
  }
  if (!token) {
    _relayDaemonCredentialRenderError({
      message: 'Paste the one-time pairing token before generating a daemon credential.',
      detail: '',
    });
    relayDaemonCredentialRefreshButtonState();
    return;
  }
  var credentialId = _relayDaemonCredentialCurrentCredentialId(rc);
  if (!skipExistingCredentialGuard && credentialId) {
    _relayDaemonCredentialSetConfirming(true, credentialId);
    relayDaemonCredentialRefreshButtonState();
    return;
  }
  _relayDaemonCredentialSetConfirming(false);
  _relayDaemonCredentialSetInFlight(true);
  if (typeof send === 'function') {
    send({ cmd: 'generate_daemon_credential', pairing_token: token });
  }
}

function relayDaemonCredentialGenerate() {
  _relayDaemonCredentialSubmit(false);
}

function relayDaemonCredentialCancelConfirm() {
  _relayDaemonCredentialSetConfirming(false);
  relayDaemonCredentialRefreshButtonState();
}

function relayDaemonCredentialConfirmGenerate() {
  _relayDaemonCredentialSubmit(true);
}

function handleRelayDaemonCredential(msg) {
  if (!msg || msg.type !== 'daemon_credential') return;
  _relayDaemonCredentialSetInFlight(false);
  _relayDaemonCredentialSetConfirming(false);
  var view = _relayDaemonCredentialComputeView(msg);
  if (view.kind === 'success') {
    _relayDaemonCredentialClearError();
    _relayDaemonCredentialClearRecovery();
    _relayDaemonCredentialApplyRelayConfig(msg);
    if (typeof document !== 'undefined' && document.getElementById) {
      var token = document.getElementById('gls-relay-daemon-credential-token');
      if (token) token.value = '';
    }
    _relayDaemonCredentialRenderSuccess(view);
    relayDaemonCredentialRefreshButtonState();
  } else if (view.kind === 'recovery') {
    _relayDaemonCredentialClearError();
    _relayDaemonCredentialClearResult();
    // Keep the pasted one-time token in place after this recoverable local
    // Settings write failure so the operator can retry after saving/revoking.
    _relayDaemonCredentialRenderRecovery(view);
    relayDaemonCredentialRefreshButtonState();
  } else if (view.kind === 'error') {
    _relayDaemonCredentialClearRecovery();
    _relayDaemonCredentialClearResult();
    _relayDaemonCredentialRenderError(view);
  }
}

/* ----------------------------------------------------------------------------
 * Relay DEVICE-LINK: display-once QR + URL (TORQUE:603 #3).
 *
 * Lets an operator mint a SINGLE-USE, short-lived pairing link and scan/copy it
 * onto a phone to establish a remote web-UI session. CONSUMER of the shipped
 * backend (do NOT modify): daemon cmd `generate_relay_device_link` (requires
 * {confirm:true}) → cloud_hooks.mint_relay_device_link → connector
 * mint_client_establish_code. Response is a one-shot command RESPONSE (NOT a
 * delta/surface), mirroring the #2 probe transport:
 *   success: { type:"relay_device_link", ok:true, code, establish_url,
 *              expires_at, owner_user_id }
 *   no confirm: { ok:false, status:"confirmation_required", message }
 *   failure: { ok:false, error, message }  (error ∈ relay_disabled |
 *              relay_not_started | mint_unsupported | mint_failed |
 *              mint_timeout | mint_invalid_response | <relay rejection>)
 *
 * SECURITY GUARDRAILS (locked, testable — see shared-memory fa989e4e4cc6):
 *  1. DISPLAY-ONCE: the minted secret (code / establish_url / QR) lives ONLY in
 *     the ephemeral `_relayDeviceLinkSecret` local for the active render. On
 *     Dismiss, on modal close, and on any relay_config / relay_connection delta
 *     refresh the secret DOM nodes are REMOVED (not merely hidden) and the local
 *     is cleared. There is NO re-display path — re-viewing requires a fresh mint.
 *  2. NEVER PERSIST CLIENT-SIDE: the secret is NEVER written to state.* /
 *     localStorage / sessionStorage / any delta-rebuildable surface. A delta
 *     arriving mid-display only flips the generate button's enabled state — it
 *     never re-injects or re-renders the secret (it is kept OUT of the
 *     delta-driven _relay*ComputeView recompute).
 *  3. NEVER MINT WITHOUT THE GATE: the command is sent ONLY with confirm:true,
 *     and ONLY after the explicit local confirm gesture (a custom inline confirm
 *     — window.confirm is broken in WKWebView). No auto-mint on panel/modal open.
 *  4. NEVER LOG: the code / establish_url are never console.log'd.
 * -------------------------------------------------------------------------- */

var _relayDeviceLinkInFlight = false;
var _relayDeviceLinkConfirming = false;
/* The active minted secret, held ONLY while the display-once panel is on
 * screen. A closure-local module var — intentionally NEVER assigned into
 * `state`, localStorage, sessionStorage, or any delta-rebuildable surface, and
 * never logged. Cleared (and its DOM nodes removed) on Dismiss and on modal
 * close; it deliberately SURVIVES routine relay section-refresh deltas
 * (engineer decision 43ab33a09a84). Display-once: no re-view path — re-viewing
 * requires a fresh mint. */
var _relayDeviceLinkSecret = null;

/* Pure view computation — no DOM. The generate button is enabled only when
 * relay is enabled AND has a relay_url configured; otherwise a hint explains
 * why. Absent / malformed `relay_config` → { visible:false } (pre-producer /
 * community build, mirroring the config sub-block). */
function _relayDeviceLinkComputeGate(rc) {
  if (!rc || typeof rc !== 'object') {
    return { visible: false, canMint: false, reason: '' };
  }
  var config = (rc.config && typeof rc.config === 'object') ? rc.config : {};
  if (!config.enabled) {
    return { visible: true, canMint: false,
      reason: 'Enable relay to generate a device link.' };
  }
  if (!String(config.relay_url || '').trim()) {
    return { visible: true, canMint: false,
      reason: 'Set the relay URL to generate a device link.' };
  }
  return { visible: true, canMint: true, reason: '' };
}

/* Pure view computation — no DOM. Maps a `relay_device_link` response to a
 * render view. Non-matching payload → { kind:'none' }. The success view carries
 * the secret fields for the immediate display-once render ONLY; callers must
 * never persist them. The error view carries NO secret (message/hint only). */
function _relayDeviceLinkComputeView(msg) {
  if (!msg || typeof msg !== 'object' || msg.type !== 'relay_device_link') {
    return { kind: 'none' };
  }
  if (msg.ok === true) {
    return {
      kind: 'success',
      code: String(msg.code || ''),
      establishUrl: String(msg.establish_url || ''),
      expiresAt: String(msg.expires_at || ''),
    };
  }
  var status = String(msg.status || '');
  var error = String(msg.error || '');
  var message = String(msg.message || '');
  // Map "not connected" failures to a clear actionable hint; never echo a secret.
  var hint = '';
  if (error === 'relay_disabled' || error === 'relay_not_started') {
    hint = 'Relay is not connected — enable and start the relay, then retry.';
  }
  if (!message) {
    message = (status === 'confirmation_required')
      ? 'Confirmation is required to mint a device link.'
      : 'Could not generate a device link.';
  }
  return { kind: 'error', status: status, error: error, message: message, hint: hint };
}

/* Pure — no DOM. Humanize an ISO8601 `expires_at` into a relative + absolute
 * pair: { relative, absolute }. relative is "expires in N seconds/minutes/
 * hours" (or "expired" when already past); it is "" when expires_at is empty or
 * unparseable so the caller omits the relative line. absolute is the raw string
 * (or "" when absent). nowMs is injectable for deterministic tests. */
function _relayDeviceLinkFormatExpiry(expiresAt, nowMs) {
  var raw = String(expiresAt || '').trim();
  if (!raw) return { relative: '', absolute: '' };
  var t = Date.parse(raw);
  if (!isFinite(t)) return { relative: '', absolute: raw };
  var now = (typeof nowMs === 'number') ? nowMs : Date.now();
  var deltaSec = Math.round((t - now) / 1000);
  if (deltaSec <= 0) return { relative: 'expired', absolute: raw };
  var rel;
  if (deltaSec < 60) {
    rel = 'expires in ' + deltaSec + (deltaSec === 1 ? ' second' : ' seconds');
  } else if (deltaSec < 3600) {
    var mins = Math.round(deltaSec / 60);
    rel = 'expires in ' + mins + (mins === 1 ? ' minute' : ' minutes');
  } else {
    var hrs = Math.round(deltaSec / 3600);
    rel = 'expires in ' + hrs + (hrs === 1 ? ' hour' : ' hours');
  }
  return { relative: rel, absolute: raw };
}

/* Generate an inline-SVG QR for the establish_url using the vendored
 * qrcode-generator global. LOCAL ONLY — the credential-bearing URL never leaves
 * the browser. typeNumber 0 = auto-fit version; 'M' error correction. Returns
 * '' if the vendored global is unavailable (community / no-vendor build) or on
 * any encode error. The URL is encoded into QR modules (numeric path data), not
 * embedded as markup text, so the returned SVG is safe to inject as-is. Never
 * logs the URL. */
function _relayDeviceLinkQrSvg(url) {
  try {
    var gen = (typeof qrcode !== 'undefined') ? qrcode
      : (typeof window !== 'undefined' ? window.qrcode : null);
    if (typeof gen !== 'function') return '';
    var qr = gen(0, 'M');
    qr.addData(String(url || ''));
    qr.make();
    return qr.createSvgTag({ cellSize: 4, margin: 4, scalable: true });
  } catch (e) {
    return '';
  }
}

/* Remove the display-once secret: drop the local AND remove the secret DOM
 * nodes (not merely hide them). Idempotent. Called only on Dismiss and on modal
 * close — routine relay section-refresh deltas deliberately preserve the secret
 * (engineer decision 43ab33a09a84). */
function _relayDeviceLinkClearSecret() {
  _relayDeviceLinkSecret = null;
  if (typeof document === 'undefined' || !document.getElementById) return;
  var panel = document.getElementById('gls-relay-device-link-panel');
  if (panel) {
    panel.innerHTML = '';  // detaches the secret child nodes from the DOM
    panel.hidden = true;
  }
}

function _relayDeviceLinkClearError() {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var el = document.getElementById('gls-relay-device-link-error');
  if (el) { el.hidden = true; el.innerHTML = ''; }
}

/* Full reset of the device-link sub-block: clear secret + confirm gesture +
 * error. Called on modal close so nothing transient leaks into the next open. */
function _relayDeviceLinkReset() {
  _relayDeviceLinkClearSecret();
  _relayDeviceLinkSetConfirming(false);
  _relayDeviceLinkClearError();
}

function _relayDeviceLinkSetConfirming(on) {
  _relayDeviceLinkConfirming = !!on;
  if (typeof document === 'undefined' || !document.getElementById) return;
  var box = document.getElementById('gls-relay-device-link-confirm');
  if (box) box.hidden = !on;
  // Hide the trigger button while the confirm gesture is up to keep one
  // unambiguous next action.
  var btn = document.getElementById('gls-relay-device-link-generate');
  if (btn) btn.hidden = !!on;
}

function _relayDeviceLinkSetInFlight(on) {
  _relayDeviceLinkInFlight = !!on;
  if (typeof document === 'undefined' || !document.getElementById) return;
  var btn = document.getElementById('gls-relay-device-link-generate');
  if (btn) {
    btn.disabled = !!on;
    btn.textContent = on ? 'Generating…' : 'Generate device link';
  }
}

/* Delta-driven refresh: flip ONLY the generate button's enabled state from the
 * gate. It deliberately does NOT touch the displayed secret: a routine
 * relay_config / relay_connection section-refresh delta must PRESERVE an
 * already-rendered device-link secret (engineer decision, durable memory
 * 43ab33a09a84). The secret is cleared ONLY on explicit Dismiss and on modal
 * close. This is safe for never-persist: the secret is never in state, so a
 * delta cannot resurrect or re-render it — we simply stop the delta from
 * destroying the live DOM node. Also preserves an in-progress confirm gesture
 * (touches only the button's disabled/title/hidden + the hint line, never the
 * confirm box) and the button element itself (so focus/caret survive). */
function _relayDeviceLinkRefreshButtonState() {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var rc = (typeof state !== 'undefined' && state) ? state.relay_config : null;
  var gate = _relayDeviceLinkComputeGate(rc);
  var btn = document.getElementById('gls-relay-device-link-generate');
  if (btn) {
    btn.disabled = !gate.canMint || _relayDeviceLinkInFlight;
    if (typeof btn.setAttribute === 'function') {
      btn.setAttribute('title', gate.canMint
        ? 'Mint a single-use, short-lived device link'
        : gate.reason);
    }
  }
  var hint = document.getElementById('gls-relay-device-link-hint');
  if (hint) {
    // Suppress the disabled-reason hint while the confirm gesture is showing.
    var showHint = !gate.canMint && !_relayDeviceLinkConfirming;
    hint.hidden = !showHint;
    hint.textContent = showHint ? gate.reason : '';
  }
}

/* Step 1 (button onclick): reveal the local confirm gesture. Does NOT mint.
 * Guards: never while a mint is in flight, never when the gate forbids it. */
function relayDeviceLinkRequest() {
  if (_relayDeviceLinkInFlight) return;
  var rc = (typeof state !== 'undefined' && state) ? state.relay_config : null;
  if (!_relayDeviceLinkComputeGate(rc).canMint) return;
  _relayDeviceLinkClearSecret();   // any prior display is gone (display-once)
  _relayDeviceLinkClearError();
  _relayDeviceLinkSetConfirming(true);
}

/* Cancel the confirm gesture without minting. */
function relayDeviceLinkCancelConfirm() {
  _relayDeviceLinkSetConfirming(false);
  _relayDeviceLinkRefreshButtonState();
}

/* Step 2 (explicit Generate inside the confirm gesture): the ONLY place the
 * mint command is sent, and ONLY with confirm:true. Mirrors the #2 probe's
 * command-dispatch path (send({cmd, ...})). */
function relayDeviceLinkConfirmGenerate() {
  if (_relayDeviceLinkInFlight) return;
  _relayDeviceLinkSetConfirming(false);
  _relayDeviceLinkClearError();
  _relayDeviceLinkSetInFlight(true);
  if (typeof send === 'function') {
    send({ cmd: 'generate_relay_device_link', confirm: true });
  }
}

/* Render the display-once panel: inline-SVG QR + copyable establish URL + raw
 * code + TTL (relative + absolute) + a Dismiss button. The secret is held only
 * in the closure-local; nothing here touches `state`. All server-supplied text
 * is esc()'d (the QR SVG is locally-generated numeric path data, injected as-
 * is). */
function _relayDeviceLinkRenderSuccess(view) {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var panel = document.getElementById('gls-relay-device-link-panel');
  if (!panel) return;
  _relayDeviceLinkSecret = {
    code: view.code, establishUrl: view.establishUrl, expiresAt: view.expiresAt,
  };
  var qrSvg = _relayDeviceLinkQrSvg(view.establishUrl);
  var ttl = _relayDeviceLinkFormatExpiry(view.expiresAt);
  var html = '';
  if (qrSvg) {
    html += '<div class="gls-relay-device-link-qr" aria-label="Device link QR code">'
      + qrSvg + '</div>';
  }
  html += '<div class="gls-relay-device-link-field">'
    + '<span class="gls-relay-device-link-field-label">Link</span>'
    + '<code class="gls-relay-device-link-url" tabindex="0">'
    + _relayProbeEsc(view.establishUrl) + '</code></div>';
  html += '<div class="gls-relay-device-link-field">'
    + '<span class="gls-relay-device-link-field-label">Code</span>'
    + '<code class="gls-relay-device-link-code" tabindex="0">'
    + _relayProbeEsc(view.code) + '</code></div>';
  if (ttl.relative) {
    html += '<div class="gls-relay-device-link-ttl">'
      + _relayProbeEsc(ttl.relative) + '</div>';
  }
  if (ttl.absolute) {
    html += '<div class="gls-relay-device-link-expires">Expires: '
      + _relayProbeEsc(ttl.absolute) + '</div>';
  }
  html += '<div class="gls-relay-device-link-note">Shown once — scan or copy now.'
    + ' Dismissing clears it; generate again for a new link.</div>';
  html += '<button type="button" class="btn-secondary"'
    + ' id="gls-relay-device-link-dismiss"'
    + ' onclick="relayDeviceLinkDismiss()">Done</button>';
  panel.innerHTML = html;
  panel.hidden = false;
}

function _relayDeviceLinkRenderError(view) {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var el = document.getElementById('gls-relay-device-link-error');
  if (!el) return;
  var html = '<span class="gls-relay-device-link-error-msg">'
    + _relayProbeEsc(view.message) + '</span>';
  if (view.hint) {
    html += '<span class="gls-relay-device-link-error-hint">'
      + _relayProbeEsc(view.hint) + '</span>';
  }
  el.hidden = false;
  el.innerHTML = html;
}

/* Dismiss the display-once panel: remove the secret nodes + clear the local. */
function relayDeviceLinkDismiss() {
  _relayDeviceLinkClearSecret();
}

/* Handle the daemon's one-shot `relay_device_link` response: clear the loading
 * state, then render the display-once panel (success) or the actionable error
 * (failure). Never logs the secret. */
function handleRelayDeviceLink(msg) {
  if (!msg || msg.type !== 'relay_device_link') return;
  _relayDeviceLinkSetInFlight(false);
  var view = _relayDeviceLinkComputeView(msg);
  if (view.kind === 'success') {
    _relayDeviceLinkClearError();
    _relayDeviceLinkRenderSuccess(view);
  } else if (view.kind === 'error') {
    _relayDeviceLinkClearSecret();
    _relayDeviceLinkRenderError(view);
  }
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    _relayStatusComputeView: _relayStatusComputeView,
    _relayConfigComputeView: _relayConfigComputeView,
    _relayProbeComputeView: _relayProbeComputeView,
    _relayDaemonCredentialComputeGate: _relayDaemonCredentialComputeGate,
    _relayDaemonCredentialComputeView: _relayDaemonCredentialComputeView,
    _relayDeviceLinkComputeGate: _relayDeviceLinkComputeGate,
    _relayDeviceLinkComputeView: _relayDeviceLinkComputeView,
    _relayDeviceLinkFormatExpiry: _relayDeviceLinkFormatExpiry,
    RELAY_STUCK_RETRY_THRESHOLD: RELAY_STUCK_RETRY_THRESHOLD,
    RELAY_PROBE_STATUS_COLORS: RELAY_PROBE_STATUS_COLORS,
  };
}

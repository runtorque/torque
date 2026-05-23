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

/* Secondary detail surface: the compact "Relay" row in the daemon-status
 * modal (Global Settings → System). Driven from `state.relay_connection`.
 * Absent field → hide the section entirely. */
function _relayStatusRenderModalRow(view, rc) {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var section = document.getElementById('gls-relay-section');
  if (!section) return;
  if (rc === undefined) {
    rc = (typeof state !== 'undefined' && state) ? state.relay_connection : null;
  }
  if (!view) view = _relayStatusComputeView(rc);

  if (!view.visible) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  _relayStatusApplyDot(document.getElementById('gls-relay-status-dot'), view);
  _relayStatusSetModalText('gls-relay-status-text', view.statusText);
  _relayStatusSetModalText('gls-relay-host', rc && rc.relay_host);
  _relayStatusSetModalText('gls-relay-retry-count',
    rc ? String(Number(rc.retry_count || 0)) : '0');
  _relayStatusSetModalText('gls-relay-since', rc && rc.since);
  _relayStatusSetModalText('gls-relay-last-error', rc && rc.last_error);
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    _relayStatusComputeView: _relayStatusComputeView,
    RELAY_STUCK_RETRY_THRESHOLD: RELAY_STUCK_RETRY_THRESHOLD,
  };
}

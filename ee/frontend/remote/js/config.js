/* Bootstrap config for the remote web UI.
 *
 * The remote UI is a pure CLIENT of the shipped relay contract. It CONSUMES a
 * session that was provisioned server-side (POST /v1/admin/client-sessions, the
 * QR-provisioning flow) — it never mints one. Config arrives via, in priority
 * order:
 *   1. a <script id="torque-remote-config" type="application/json"> tag, and
 *   2. URL query/hash params (override individual fields; handy for local dev).
 *
 * Auth modes (see plan O1):
 *   - cookie-mode (primary): the relay origin holds an HttpOnly torque_session
 *     cookie that the browser attaches to BOTH the WS upgrade and the HTTP POST.
 *     No token lives in JS. This is the only mode browsers can use for the WS
 *     handshake (the WebSocket constructor cannot set an Authorization header).
 *   - bearer-mode: a session token is held in memory and sent as
 *     `Authorization: Bearer` on HTTP POST. Only meaningful for
 *     local-dev-unauthenticated relays (where no token is needed at all) or the
 *     HTTP send path. WS in bearer-mode falls back to a `?token=` query param,
 *     which is local-dev only.
 *
 * Exposed on globalThis as RemoteConfig for the browser bundle + Node tests.
 */
(function() {
  var root = (typeof globalThis !== 'undefined') ? globalThis : this;

  function _trim(value) {
    return String(value == null ? '' : value).trim();
  }

  function _stripTrailingSlash(url) {
    return _trim(url).replace(/\/+$/, '');
  }

  function _truthy(value) {
    var v = _trim(value).toLowerCase();
    return v === '1' || v === 'true' || v === 'yes' || v === 'on';
  }

  // Parse a config object (already merged from tag + params) into the normalized
  // shape the rest of the bundle consumes. Pure + deterministic for tests.
  function parseConfig(input) {
    var raw = input && typeof input === 'object' ? input : {};
    var relayBaseUrl = _stripTrailingSlash(raw.relayBaseUrl || raw.relay_base_url || '');
    var daemonId = _trim(raw.daemonId || raw.daemon_id || '');
    var sessionToken = _trim(raw.sessionToken || raw.session_token || '');
    var clientId = _trim(raw.clientId || raw.client_id || '');
    var authMode = _trim(raw.authMode || raw.auth_mode || '').toLowerCase();
    if (authMode !== 'cookie' && authMode !== 'bearer') {
      // Default: bearer when a token was handed to JS, else cookie.
      authMode = sessionToken ? 'bearer' : 'cookie';
    }
    var askEnabled = raw.askEnabled !== undefined
      ? !!raw.askEnabled
      : (raw.ask_enabled !== undefined ? _truthy(raw.ask_enabled) : false);
    var errors = [];
    if (!relayBaseUrl) errors.push('relayBaseUrl is required');
    if (!daemonId) errors.push('daemonId is required');
    return {
      relayBaseUrl: relayBaseUrl,
      daemonId: daemonId,
      sessionToken: sessionToken,
      authMode: authMode,
      clientId: clientId,
      // Ask UI is gated OFF by default: ask render + ask answering only light up
      // once TORQUE:534 + the connector user-destination egress gating land and
      // a clean user-destined ask stream exists. Until then the conversation
      // surface (user_message/agent_message) ships unblocked. The flag never
      // implies client-side recipient filtering — user-destination is resolved
      // server-side.
      askEnabled: askEnabled,
      valid: errors.length === 0,
      errors: errors,
    };
  }

  // Convert relayBaseUrl ("http(s)://host") into the ws(s):// scheme.
  function _wsScheme(baseUrl) {
    var base = _stripTrailingSlash(baseUrl);
    if (base.indexOf('https://') === 0) return 'wss://' + base.slice('https://'.length);
    if (base.indexOf('http://') === 0) return 'ws://' + base.slice('http://'.length);
    if (base.indexOf('wss://') === 0 || base.indexOf('ws://') === 0) return base;
    return base;
  }

  function wsUrl(cfg) {
    if (!cfg || !cfg.relayBaseUrl || !cfg.daemonId) return '';
    var url = _wsScheme(cfg.relayBaseUrl)
      + '/v1/client/' + encodeURIComponent(cfg.daemonId) + '/ws';
    var query = [];
    if (cfg.clientId) query.push('client_id=' + encodeURIComponent(cfg.clientId));
    // bearer-mode WS auth has no header path in browsers; ?token= is local-dev
    // only and never used in cookie-mode.
    if (cfg.authMode === 'bearer' && cfg.sessionToken) {
      query.push('token=' + encodeURIComponent(cfg.sessionToken));
    }
    return query.length ? url + '?' + query.join('&') : url;
  }

  function messagesUrl(cfg) {
    if (!cfg || !cfg.relayBaseUrl || !cfg.daemonId) return '';
    return _stripTrailingSlash(cfg.relayBaseUrl)
      + '/v1/messages/' + encodeURIComponent(cfg.daemonId);
  }

  // Headers for the HTTP POST send path. Cookie-mode relies on the browser
  // attaching torque_session automatically (credentials: 'include').
  function httpHeaders(cfg) {
    var headers = { 'content-type': 'application/json' };
    if (cfg && cfg.authMode === 'bearer' && cfg.sessionToken) {
      headers['authorization'] = 'Bearer ' + cfg.sessionToken;
    }
    return headers;
  }

  // Browser-only: read + merge the config tag and URL params.
  function loadFromDocument(documentRef, locationRef) {
    var docRef = documentRef || (typeof document !== 'undefined' ? document : null);
    var loc = locationRef || (typeof location !== 'undefined' ? location : null);
    var merged = {};
    if (docRef && docRef.getElementById) {
      var tag = docRef.getElementById('torque-remote-config');
      if (tag && tag.textContent) {
        try {
          var parsed = JSON.parse(tag.textContent);
          if (parsed && typeof parsed === 'object') merged = parsed;
        } catch (_e) { /* ignore malformed config tag */ }
      }
    }
    if (loc) {
      var params = _paramsFrom(loc);
      ['relayBaseUrl', 'relay_base_url', 'daemonId', 'daemon_id', 'sessionToken',
        'session_token', 'authMode', 'auth_mode', 'clientId', 'client_id',
        'askEnabled', 'ask_enabled', 'ask'].forEach(function(name) {
        if (params[name] !== undefined) merged[name] = params[name];
      });
      if (params.ask !== undefined && merged.ask_enabled === undefined
        && merged.askEnabled === undefined) {
        merged.ask_enabled = params.ask;
      }
    }
    return parseConfig(merged);
  }

  function _paramsFrom(loc) {
    var out = {};
    var sources = [loc.search || '', (loc.hash || '').replace(/^#/, '')];
    for (var s = 0; s < sources.length; s++) {
      var qs = sources[s].replace(/^\?/, '');
      if (!qs) continue;
      qs.split('&').forEach(function(pair) {
        if (!pair) return;
        var idx = pair.indexOf('=');
        var key = decodeURIComponent(idx >= 0 ? pair.slice(0, idx) : pair);
        var val = idx >= 0 ? decodeURIComponent(pair.slice(idx + 1)) : '';
        out[key] = val;
      });
    }
    return out;
  }

  root.RemoteConfig = {
    parseConfig: parseConfig,
    wsUrl: wsUrl,
    messagesUrl: messagesUrl,
    httpHeaders: httpHeaders,
    loadFromDocument: loadFromDocument,
  };
})();

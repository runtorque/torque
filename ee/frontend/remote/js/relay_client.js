/* Relay client for the remote web UI: WS connect + reconnect/re-auth state
 * machine, envelope id de-dupe, outbound queue, and ack/error correlation.
 *
 * Close-code policy (plan §0.1, verified against shipped relay code):
 *   - 4003 (authenticated_session_revoked / client_attach_rejected): the
 *     session is dead — owner-change/revoke/expiry. Transition to the terminal
 *     `reauth_required` state, surface a re-provision prompt, and STOP. Never
 *     tight-retry a dead token.
 *   - every other close (4001/4000/1006/1011/normal): transient — reconnect
 *     with exponential backoff using the SAME session.
 *   4001/4000 are daemon-socket closes in the relay; a client normally only
 *   sees them indirectly, but we handle them defensively as reconnectable.
 *
 * Delivery is at-least-once: inbound ids are de-duped through a bounded LRU, and
 * we ack data envelopes so the relay stops replaying them on reconnect.
 *
 * Dependencies are injected (socketFactory, httpPost, scheduler, now) so the
 * whole state machine is unit-testable without a real WebSocket. Exposed on
 * globalThis as RemoteRelayClient.
 */
(function() {
  var root = (typeof globalThis !== 'undefined') ? globalThis : this;
  var Envelope = root.RemoteEnvelope;

  var STATUS = {
    IDLE: 'idle',
    CONNECTING: 'connecting',
    READY: 'ready',
    RECONNECTING: 'reconnecting',
    REAUTH_REQUIRED: 'reauth_required',
    CLOSED: 'closed',
  };

  var DEFAULT_BACKOFF = { baseMs: 1000, capMs: 30000, jitter: 0.3 };
  var DEDUPE_CAP = 2000;
  var DATA_KINDS = { agent_message: true, ask: true, ask_reply: true };

  function RemoteRelayClient(opts) {
    opts = opts || {};
    this.config = opts.config;
    this.store = opts.store;
    this.envelope = opts.envelope || Envelope;
    // socketFactory(url) -> a socket exposing send(str), close(), and the
    // onopen/onmessage/onclose/onerror handler slots (WebSocket-shaped).
    this._socketFactory = opts.socketFactory || _defaultSocketFactory;
    this._httpPost = opts.httpPost || null;       // (url, headers, bodyStr) -> Promise
    this._setTimeout = opts.setTimeout || (typeof setTimeout !== 'undefined' ? setTimeout : null);
    this._clearTimeout = opts.clearTimeout || (typeof clearTimeout !== 'undefined' ? clearTimeout : null);
    this._now = opts.now || function() { return Date.now(); };
    this._onStatus = opts.onStatus || function() {};
    this.backoff = Object.assign({}, DEFAULT_BACKOFF, opts.backoff || {});

    this.status = STATUS.IDLE;
    this.epoch = 0;
    this.lastReady = null;
    this.lastCloseCode = null;
    this._socket = null;
    this._reconnectAttempts = 0;
    this._reconnectTimer = null;
    this._intentionalClose = false;
    this._outboundQueue = [];          // envelopes awaiting a READY socket
    this._pendingOutbound = {};        // envId -> envelope (awaiting ack/error)
    this._seenIds = [];                // LRU order
    this._seenSet = {};                // id -> true
  }

  RemoteRelayClient.prototype._setStatus = function(status, detail) {
    if (this.status === status) {
      this._onStatus(status, detail || {});
      return;
    }
    this.status = status;
    this._onStatus(status, detail || {});
  };

  // --- de-dupe ------------------------------------------------------------
  RemoteRelayClient.prototype._seen = function(id) {
    if (!id) return false;
    if (this._seenSet[id]) return true;
    this._seenSet[id] = true;
    this._seenIds.push(id);
    if (this._seenIds.length > DEDUPE_CAP) {
      var evicted = this._seenIds.shift();
      delete this._seenSet[evicted];
    }
    return false;
  };

  // --- connect / reconnect ------------------------------------------------
  RemoteRelayClient.prototype.connect = function() {
    if (this.status === STATUS.REAUTH_REQUIRED) return; // dead token; require re-provision
    this._intentionalClose = false;
    var url = root.RemoteConfig ? root.RemoteConfig.wsUrl(this.config) : this.config.wsUrl;
    this._setStatus(STATUS.CONNECTING);
    var socket;
    try {
      socket = this._socketFactory(url);
    } catch (err) {
      this._scheduleReconnect();
      return;
    }
    this._socket = socket;
    var self = this;
    socket.onopen = function() { /* await `ready` envelope before flushing */ };
    socket.onmessage = function(event) {
      self._onMessage(event && event.data !== undefined ? event.data : event);
    };
    socket.onclose = function(event) {
      self._onClose(event && typeof event.code === 'number' ? event.code : (event || 0));
    };
    socket.onerror = function() {
      // Let the subsequent close drive state; just ensure the socket closes.
      try { if (socket.close) socket.close(); } catch (_e) { /* noop */ }
    };
  };

  RemoteRelayClient.prototype._onClose = function(code) {
    this.lastCloseCode = code;
    this._socket = null;
    if (code === 4003) {
      // Session revoked / attach rejected: terminal until re-provisioned.
      this._setStatus(STATUS.REAUTH_REQUIRED, { code: code });
      return;
    }
    if (this._intentionalClose) {
      this._setStatus(STATUS.CLOSED, { code: code });
      return;
    }
    this._scheduleReconnect(code);
  };

  RemoteRelayClient.prototype._scheduleReconnect = function(code) {
    this._setStatus(STATUS.RECONNECTING, { code: code, attempt: this._reconnectAttempts + 1 });
    var delay = this._backoffDelay(this._reconnectAttempts);
    this._reconnectAttempts += 1;
    if (!this._setTimeout) return; // no scheduler (test will call connect() manually)
    var self = this;
    this._reconnectTimer = this._setTimeout(function() {
      self._reconnectTimer = null;
      self.connect();
    }, delay);
  };

  RemoteRelayClient.prototype._backoffDelay = function(attempt) {
    var base = this.backoff.baseMs * Math.pow(2, Math.min(attempt, 10));
    var capped = Math.min(base, this.backoff.capMs);
    var jitter = capped * this.backoff.jitter * (Math.random() * 2 - 1);
    return Math.max(0, Math.round(capped + jitter));
  };

  // --- inbound ------------------------------------------------------------
  RemoteRelayClient.prototype._onMessage = function(data) {
    var env;
    try {
      env = this.envelope.parseInbound(data);
    } catch (_e) {
      return; // drop malformed frame
    }
    if (this._seen(env.id)) return; // at-least-once de-dupe

    switch (env.kind) {
      case 'ready':
        this._handleReady(env);
        return;
      case 'ping':
        this._sendControl('pong', env);
        return;
      case 'pong':
        return;
      case 'ack':
        this._handleAck(env);
        return;
      case 'error':
        this._handleError(env);
        return;
      default:
        break;
    }
    if (DATA_KINDS[env.kind]) {
      if (this.store) this.store.ingestInbound(env);
      this._ackInbound(env);
    }
  };

  RemoteRelayClient.prototype._handleReady = function(env) {
    var payload = env.payload || {};
    var epoch = Number(payload.epoch);
    this.epoch = isFinite(epoch) ? epoch : this.epoch;
    this.lastReady = payload;
    this._reconnectAttempts = 0; // reset backoff on a clean ready
    this._setStatus(STATUS.READY, { epoch: this.epoch, replayed: payload.replayed });
    this._flushOutbound();
  };

  RemoteRelayClient.prototype._handleAck = function(env) {
    var ackId = (env.payload || {}).ack_id;
    if (!ackId) return;
    delete this._pendingOutbound[ackId];
    if (this.store) {
      this.store.markDelivery(ackId, (env.payload || {}).delivery_state || 'acked',
        (env.payload || {}).reason);
    }
  };

  RemoteRelayClient.prototype._handleError = function(env) {
    var refId = (env.payload || {}).ref_id;
    if (refId && this._pendingOutbound[refId]) {
      delete this._pendingOutbound[refId];
      if (this.store) {
        this.store.markDelivery(refId, 'failed', (env.payload || {}).message || 'error');
      }
      return;
    }
    this._onStatus('error', { payload: env.payload || {} });
  };

  RemoteRelayClient.prototype._ackInbound = function(env) {
    if (!this.config) return;
    var ack;
    try {
      ack = this.envelope.buildAck({
        daemonId: this.config.daemonId,
        clientId: this.config.clientId,
        ackId: env.id,
        ackKind: env.kind,
        deliveryState: 'acked',
      });
    } catch (_e) { return; }
    this._rawSend(ack);
  };

  RemoteRelayClient.prototype._sendControl = function(kind, refEnv) {
    try {
      var control = this.envelope.makeEnvelope({
        daemon_id: this.config.daemonId,
        source: { kind: 'remote-client', id: this.config.clientId || 'remote-client' },
        target: { kind: 'daemon', id: this.config.daemonId },
        kind: kind,
        payload: {},
      });
      this._rawSend(control);
    } catch (_e) { /* noop */ }
  };

  // --- outbound -----------------------------------------------------------
  // Send a user message (or ask answer via replyToId) to an agent.
  RemoteRelayClient.prototype.sendUserMessage = function(agentId, text, opts) {
    opts = opts || {};
    var env = this.envelope.buildUserMessage({
      daemonId: this.config.daemonId,
      clientId: this.config.clientId,
      agentId: agentId,
      message: text,
      threadId: opts.threadId,
      replyToId: opts.replyToId,
    });
    if (this.store) this.store.recordOutbound(env);
    this._enqueue(env);
    return env;
  };

  RemoteRelayClient.prototype._enqueue = function(env) {
    this._pendingOutbound[env.id] = env;
    if (this.status === STATUS.READY && this._socket) {
      this._rawSend(env);
    } else {
      this._outboundQueue.push(env);
    }
  };

  RemoteRelayClient.prototype._flushOutbound = function() {
    var queue = this._outboundQueue;
    this._outboundQueue = [];
    for (var i = 0; i < queue.length; i++) this._rawSend(queue[i]);
  };

  RemoteRelayClient.prototype._rawSend = function(env) {
    if (!this._socket || typeof this._socket.send !== 'function') {
      // No live socket: keep it queued for the next READY.
      if (this._outboundQueue.indexOf(env) < 0 && env.kind === 'user_message') {
        this._outboundQueue.push(env);
      }
      return false;
    }
    try {
      this._socket.send(JSON.stringify(env));
      return true;
    } catch (_e) {
      if (env.kind === 'user_message') this._outboundQueue.push(env);
      return false;
    }
  };

  // HTTP fallback send path (POST /v1/messages/{daemonId}). Optional; the WS
  // path is primary. Returns a Promise when an httpPost impl is configured.
  RemoteRelayClient.prototype.sendViaHttp = function(env) {
    if (!this._httpPost || !root.RemoteConfig) return Promise.resolve(null);
    var url = root.RemoteConfig.messagesUrl(this.config);
    var headers = root.RemoteConfig.httpHeaders(this.config);
    return this._httpPost(url, headers, JSON.stringify(env));
  };

  RemoteRelayClient.prototype.close = function() {
    this._intentionalClose = true;
    if (this._reconnectTimer && this._clearTimeout) {
      this._clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
    if (this._socket && this._socket.close) {
      try { this._socket.close(1000, 'client_closed'); } catch (_e) { /* noop */ }
    }
    this._socket = null;
    this._setStatus(STATUS.CLOSED);
  };

  function _defaultSocketFactory(url) {
    if (typeof WebSocket === 'undefined') {
      throw new Error('WebSocket is not available in this environment');
    }
    return new WebSocket(url);
  }

  RemoteRelayClient.STATUS = STATUS;
  root.RemoteRelayClient = RemoteRelayClient;
})();

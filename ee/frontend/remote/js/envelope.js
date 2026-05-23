/* Relay wire-envelope build/validate/parse for the remote web UI.
 *
 * Mirrors the relevant parts of ee/relay/src/core/protocol.ts so the client
 * produces envelopes the relay accepts and rejects malformed inbound frames
 * fast. RELAY_PROTOCOL_VERSION is 1; every envelope sets v:1.
 *
 * Envelope: { v:1, id, trace_id?, daemon_id, source, target, kind,
 *             created_at(ISO-8601), payload:{} }
 * Endpoint: { kind:"daemon"|"remote-client"|"channel"|"relay", id, user_id?, platform? }
 *
 * IMPORTANT contract note (verified against connector.py:239): the local daemon
 * connector accepts ONLY `user_message` inbound; any other kind returns an
 * `unsupported_relay_kind` error. The user's ANSWER to an `ask` is therefore
 * sent as a `user_message` carrying `reply_to_id` = the ask id — NOT as
 * kind:"ask_reply" (which is an EGRESS-only mirror, agent->client). This is a
 * deliberate correction to the plan §7 wording; the plan's ingress payload
 * allowlist already lists reply_to_id.
 *
 * Exposed on globalThis as RemoteEnvelope for the browser bundle + Node tests.
 */
(function() {
  var root = (typeof globalThis !== 'undefined') ? globalThis : this;

  var RELAY_PROTOCOL_VERSION = 1;
  var ENDPOINT_KINDS = ['daemon', 'remote-client', 'channel', 'relay'];
  var MESSAGE_KINDS = [
    'hello', 'ready', 'ping', 'pong', 'snapshot_request', 'snapshot',
    'user_message', 'agent_message', 'ask', 'ask_reply', 'ack', 'error',
    'channel_event',
  ];

  function _trim(value) {
    return String(value == null ? '' : value).trim();
  }

  function newId(prefix) {
    var p = prefix || 'msg';
    var cryptoRef = (typeof root.crypto !== 'undefined') ? root.crypto : null;
    if (cryptoRef && typeof cryptoRef.randomUUID === 'function') {
      return p + '-' + cryptoRef.randomUUID();
    }
    return p + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
  }

  function nowIso() {
    return new Date().toISOString();
  }

  function isMessageKind(kind) {
    return MESSAGE_KINDS.indexOf(kind) >= 0;
  }

  function _isPlainObject(value) {
    return !!value && typeof value === 'object' && !Array.isArray(value);
  }

  function _normalizeEndpoint(value, field) {
    if (!_isPlainObject(value)) throw new Error(field + ' endpoint is required');
    var kind = _trim(value.kind);
    if (ENDPOINT_KINDS.indexOf(kind) < 0) throw new Error(field + '.kind is invalid: ' + kind);
    var id = _trim(value.id);
    if (!id) throw new Error(field + '.id is required');
    var out = { kind: kind, id: id };
    if (_trim(value.user_id)) out.user_id = _trim(value.user_id);
    if (_trim(value.platform)) out.platform = _trim(value.platform);
    return out;
  }

  // Enforce the relay's load-bearing semantic rules client-side so we never ship
  // an envelope the relay will reject (mirrors validateEnvelopeSemantics).
  function _validateSemantics(env) {
    if (env.source.kind === 'daemon' && env.source.id !== env.daemon_id) {
      throw new Error('source daemon id must match daemon_id');
    }
    if (env.target.kind === 'daemon' && env.target.id !== env.daemon_id) {
      throw new Error('target daemon id must match daemon_id');
    }
    if (env.kind === 'ack' && !_trim((env.payload || {}).ack_id)) {
      throw new Error('ack payload.ack_id is required');
    }
    if (env.kind === 'error') {
      if (!_trim((env.payload || {}).code)) throw new Error('error payload.code is required');
      if (!_trim((env.payload || {}).message)) throw new Error('error payload.message is required');
    }
    if (!isFinite(Date.parse(env.created_at))) {
      throw new Error('created_at must be an ISO-8601 timestamp');
    }
    if (!_isPlainObject(env.payload)) throw new Error('payload must be an object');
  }

  function makeEnvelope(args) {
    var daemonId = _trim(args.daemon_id);
    if (!daemonId) throw new Error('daemon_id is required');
    var kind = _trim(args.kind);
    if (!isMessageKind(kind)) throw new Error('kind is invalid: ' + kind);
    var env = {
      v: RELAY_PROTOCOL_VERSION,
      id: _trim(args.id) || newId(kind === 'ack' ? 'ack' : 'msg'),
      daemon_id: daemonId,
      source: _normalizeEndpoint(args.source, 'source'),
      target: _normalizeEndpoint(args.target, 'target'),
      kind: kind,
      created_at: _trim(args.created_at) || nowIso(),
      payload: _isPlainObject(args.payload) ? args.payload : {},
    };
    if (_trim(args.trace_id)) env.trace_id = _trim(args.trace_id);
    _validateSemantics(env);
    return env;
  }

  // Build a client->daemon user_message. Used for both plain messages and ask
  // answers (pass replyToId for the latter). The relay overwrites
  // source.user_id to "user" regardless, so we omit it.
  function buildUserMessage(args) {
    var payload = { agent_id: _trim(args.agentId) };
    var message = (args.message == null) ? '' : String(args.message);
    payload.message = message;
    if (_trim(args.threadId)) payload.thread_id = _trim(args.threadId);
    if (_trim(args.replyToId)) payload.reply_to_id = _trim(args.replyToId);
    if (_trim(args.idempotencyKey)) payload.idempotency_key = _trim(args.idempotencyKey);
    return makeEnvelope({
      daemon_id: args.daemonId,
      id: args.id,
      source: { kind: 'remote-client', id: _trim(args.clientId) || 'remote-client' },
      target: { kind: 'daemon', id: _trim(args.daemonId) },
      kind: 'user_message',
      payload: payload,
    });
  }

  // Build a client ack for an inbound data envelope so the relay can stop
  // replaying it on reconnect. delivery_state defaults to "acked".
  function buildAck(args) {
    var payload = { ack_id: _trim(args.ackId) };
    if (args.ackKind) payload.ack_kind = _trim(args.ackKind);
    payload.delivery_state = _trim(args.deliveryState) || 'acked';
    if (_trim(args.reason)) payload.reason = _trim(args.reason);
    return makeEnvelope({
      daemon_id: args.daemonId,
      source: { kind: 'remote-client', id: _trim(args.clientId) || 'remote-client' },
      target: { kind: 'daemon', id: _trim(args.daemonId) },
      kind: 'ack',
      payload: payload,
    });
  }

  // Parse + validate an inbound frame (string or object). Throws on malformed
  // input so the caller can drop it without corrupting state.
  function parseInbound(input) {
    var raw = input;
    if (typeof input === 'string') {
      raw = JSON.parse(input);
    }
    if (!_isPlainObject(raw)) throw new Error('relay envelope must be an object');
    if (raw.v !== RELAY_PROTOCOL_VERSION) {
      throw new Error('unsupported relay protocol version: ' + String(raw.v));
    }
    var id = _trim(raw.id);
    if (!id) throw new Error('id is required');
    var env = {
      v: RELAY_PROTOCOL_VERSION,
      id: id,
      daemon_id: _trim(raw.daemon_id),
      source: _normalizeEndpoint(raw.source, 'source'),
      target: _normalizeEndpoint(raw.target, 'target'),
      kind: _trim(raw.kind),
      created_at: _trim(raw.created_at),
      payload: _isPlainObject(raw.payload) ? raw.payload : {},
    };
    if (!env.daemon_id) throw new Error('daemon_id is required');
    if (!isMessageKind(env.kind)) throw new Error('kind is invalid: ' + env.kind);
    if (_trim(raw.trace_id)) env.trace_id = _trim(raw.trace_id);
    _validateSemantics(env);
    return env;
  }

  root.RemoteEnvelope = {
    RELAY_PROTOCOL_VERSION: RELAY_PROTOCOL_VERSION,
    MESSAGE_KINDS: MESSAGE_KINDS,
    ENDPOINT_KINDS: ENDPOINT_KINDS,
    newId: newId,
    nowIso: nowIso,
    isMessageKind: isMessageKind,
    makeEnvelope: makeEnvelope,
    buildUserMessage: buildUserMessage,
    buildAck: buildAck,
    parseInbound: parseInbound,
  };
})();

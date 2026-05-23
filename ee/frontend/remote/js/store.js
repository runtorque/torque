/* In-memory conversation model for the remote web UI.
 *
 * Holds switchable USER<->AGENT conversations keyed by agent_id, derived purely
 * from observed traffic (there is no agent roster in V1 — see plan §0.3; a
 * conversation appears the first time an agent messages, and the user cannot
 * initiate to an agent that has never messaged). Inbound envelopes are mapped
 * to messages; outbound user messages are appended optimistically and have
 * their delivery state advanced by acks/errors.
 *
 * Idempotent on message id (upsert), so at-least-once delivery + reconnect
 * replay never duplicate a bubble even if the relay_client dedupe is bypassed.
 * Per-conversation history is tail-capped to keep memory bounded.
 *
 * Exposed on globalThis as RemoteStore for the browser bundle + Node tests.
 */
(function() {
  var root = (typeof globalThis !== 'undefined') ? globalThis : this;

  var DEFAULT_TAIL_CAP = 500;

  function _trim(value) {
    return String(value == null ? '' : value).trim();
  }

  // Resolve which agent a conversation envelope belongs to. Prefer the explicit
  // payload.agent_id the connector stamps; fall back to the non-"user" peer id.
  function _agentIdFromPayload(payload) {
    var explicit = _trim(payload.agent_id);
    if (explicit) return explicit;
    var pairs = [['sender_kind', 'sender_id'], ['recipient_kind', 'recipient_id']];
    for (var i = 0; i < pairs.length; i++) {
      var kind = _trim(payload[pairs[i][0]]);
      var id = _trim(payload[pairs[i][1]]);
      if (kind && kind !== 'user' && id) return id;
    }
    return '';
  }

  function _agentNameFromPayload(payload, agentId) {
    if (_trim(payload.sender_kind) && _trim(payload.sender_kind) !== 'user') {
      if (_trim(payload.sender_name)) return _trim(payload.sender_name);
    }
    if (_trim(payload.recipient_kind) && _trim(payload.recipient_kind) !== 'user') {
      if (_trim(payload.recipient_name)) return _trim(payload.recipient_name);
    }
    return agentId;
  }

  function _createdAtMs(payload, fallbackIso) {
    var raw = payload.created_at || fallbackIso;
    var parsed = Date.parse(raw);
    return isFinite(parsed) ? parsed : Date.now();
  }

  function RemoteStore(opts) {
    opts = opts || {};
    this.tailCap = opts.tailCap || DEFAULT_TAIL_CAP;
    this.conversations = {};       // agentId -> conversation
    this.order = [];               // agentId list, most-recent-activity first
    this.activeAgentId = '';
    this._subscribers = [];
  }

  RemoteStore.prototype.subscribe = function(fn) {
    if (typeof fn === 'function') this._subscribers.push(fn);
    var self = this;
    return function() {
      self._subscribers = self._subscribers.filter(function(f) { return f !== fn; });
    };
  };

  RemoteStore.prototype._notify = function() {
    for (var i = 0; i < this._subscribers.length; i++) {
      try { this._subscribers[i](this); } catch (_e) { /* subscriber error is non-fatal */ }
    }
  };

  RemoteStore.prototype._ensureConversation = function(agentId, agentName) {
    var conv = this.conversations[agentId];
    if (!conv) {
      conv = {
        agentId: agentId,
        agentName: agentName || agentId,
        messages: [],
        messageIndex: {},      // id -> message (idempotent upsert)
        pendingAsks: {},       // askId -> message
        lastActivityMs: 0,
        unread: 0,
      };
      this.conversations[agentId] = conv;
      this.order.push(agentId);
      if (!this.activeAgentId) this.activeAgentId = agentId;
    } else if (agentName && conv.agentName === agentId) {
      conv.agentName = agentName;
    }
    return conv;
  };

  RemoteStore.prototype._touch = function(agentId, atMs) {
    var conv = this.conversations[agentId];
    if (!conv) return;
    if (atMs > conv.lastActivityMs) conv.lastActivityMs = atMs;
    // Re-sort order by lastActivityMs desc, stable on insertion order.
    this.order.sort(function(a, b) {
      return (this.conversations[b].lastActivityMs || 0)
        - (this.conversations[a].lastActivityMs || 0);
    }.bind(this));
  };

  RemoteStore.prototype._appendOrUpsert = function(conv, msg) {
    var existing = conv.messageIndex[msg.id];
    if (existing) {
      // Idempotent merge: keep the stronger delivery state, refresh fields.
      for (var k in msg) {
        if (Object.prototype.hasOwnProperty.call(msg, k) && msg[k] !== undefined) {
          existing[k] = msg[k];
        }
      }
      return existing;
    }
    conv.messages.push(msg);
    conv.messageIndex[msg.id] = msg;
    if (conv.messages.length > this.tailCap) {
      var dropped = conv.messages.splice(0, conv.messages.length - this.tailCap);
      for (var d = 0; d < dropped.length; d++) delete conv.messageIndex[dropped[d].id];
    }
    return msg;
  };

  // Ingest an inbound conversation envelope (agent_message | ask | ask_reply).
  // Returns the resulting message, or null if it is not a conversation kind.
  RemoteStore.prototype.ingestInbound = function(env) {
    if (!env || !env.kind) return null;
    if (env.kind !== 'agent_message' && env.kind !== 'ask' && env.kind !== 'ask_reply') {
      return null;
    }
    var payload = env.payload || {};
    var agentId = _agentIdFromPayload(payload);
    if (!agentId) return null;
    var agentName = _agentNameFromPayload(payload, agentId);
    var conv = this._ensureConversation(agentId, agentName);
    var atMs = _createdAtMs(payload, env.created_at);
    // Prefer the stable underlying message_id when present so an agent_message
    // and its later edits/acks collapse onto one bubble; fall back to env id.
    var id = _trim(payload.message_id) || _trim(env.id);
    var messageType = _trim(payload.message_type)
      || (env.kind === 'ask' ? 'ask' : (env.kind === 'ask_reply' ? 'ask_reply' : 'message'));
    var msg = {
      id: id,
      kind: env.kind,
      agentId: agentId,
      sender: 'agent',
      body: (payload.message == null) ? '' : String(payload.message),
      messageType: messageType,
      blocking: !!payload.blocking,
      threadId: _trim(payload.thread_id),
      replyToId: _trim(payload.reply_to_id),
      createdAtMs: atMs,
      deliveryState: _trim(payload.delivery_state) || 'delivered',
    };
    this._appendOrUpsert(conv, msg);

    if (env.kind === 'ask' && msg.blocking) {
      conv.pendingAsks[msg.id] = msg;
    }
    // An ask_reply (or any reply) referencing a pending ask resolves it.
    if (msg.replyToId && conv.pendingAsks[msg.replyToId]) {
      delete conv.pendingAsks[msg.replyToId];
    }
    if (agentId !== this.activeAgentId) conv.unread += 1;
    this._touch(agentId, atMs);
    this._notify();
    return msg;
  };

  // Record an outbound user message/ask-answer optimistically (pending).
  RemoteStore.prototype.recordOutbound = function(env) {
    var payload = env.payload || {};
    var agentId = _trim(payload.agent_id);
    if (!agentId) return null;
    var conv = this._ensureConversation(agentId, this.conversations[agentId]
      ? this.conversations[agentId].agentName : agentId);
    var atMs = _createdAtMs(payload, env.created_at);
    var msg = {
      id: _trim(env.id),
      kind: 'user_message',
      agentId: agentId,
      sender: 'user',
      body: (payload.message == null) ? '' : String(payload.message),
      messageType: payload.reply_to_id ? 'ask_answer' : 'message',
      blocking: false,
      threadId: _trim(payload.thread_id),
      replyToId: _trim(payload.reply_to_id),
      createdAtMs: atMs,
      deliveryState: 'pending',
    };
    this._appendOrUpsert(conv, msg);
    // Answering an ask locally clears its pending state immediately.
    if (msg.replyToId && conv.pendingAsks[msg.replyToId]) {
      delete conv.pendingAsks[msg.replyToId];
    }
    this._touch(agentId, atMs);
    this._notify();
    return msg;
  };

  // Advance the delivery state of an outbound message by its envelope id.
  RemoteStore.prototype.markDelivery = function(envId, state, reason) {
    var id = _trim(envId);
    if (!id) return false;
    var changed = false;
    for (var agentId in this.conversations) {
      if (!Object.prototype.hasOwnProperty.call(this.conversations, agentId)) continue;
      var msg = this.conversations[agentId].messageIndex[id];
      if (msg && msg.sender === 'user') {
        msg.deliveryState = _trim(state) || msg.deliveryState;
        if (reason) msg.deliveryReason = _trim(reason);
        changed = true;
      }
    }
    if (changed) this._notify();
    return changed;
  };

  RemoteStore.prototype.setActiveAgent = function(agentId) {
    var id = _trim(agentId);
    if (!this.conversations[id]) return false;
    this.activeAgentId = id;
    this.conversations[id].unread = 0;
    this._notify();
    return true;
  };

  RemoteStore.prototype.agentList = function() {
    var self = this;
    return this.order.map(function(agentId) {
      var conv = self.conversations[agentId];
      var last = conv.messages.length ? conv.messages[conv.messages.length - 1] : null;
      return {
        agentId: agentId,
        agentName: conv.agentName,
        unread: conv.unread,
        pendingAskCount: Object.keys(conv.pendingAsks).length,
        lastBody: last ? last.body : '',
        lastActivityMs: conv.lastActivityMs,
        active: agentId === self.activeAgentId,
      };
    });
  };

  RemoteStore.prototype.activeConversation = function() {
    return this.activeAgentId ? this.conversations[this.activeAgentId] || null : null;
  };

  RemoteStore.prototype.pendingAsks = function(agentId) {
    var conv = this.conversations[_trim(agentId)];
    if (!conv) return [];
    return Object.keys(conv.pendingAsks).map(function(k) { return conv.pendingAsks[k]; });
  };

  root.RemoteStore = RemoteStore;
})();

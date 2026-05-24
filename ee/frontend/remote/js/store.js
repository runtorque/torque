/* In-memory conversation model for the remote web UI.
 *
 * Holds switchable USER<->AGENT conversations keyed by agent_id, derived from
 * observed traffic plus the optional snapshot agent roster. A conversation
 * appears the first time an agent messages, and a snapshot roster can pre-seed
 * conversations with hierarchy metadata. Inbound envelopes are mapped to
 * messages; outbound user messages are appended optimistically and have their
 * delivery state advanced by acks/errors.
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

  // Delivery-state strength ladder, mirroring the glyph progression rendered in
  // render_remote.js (pending '·' → delivered '✓' → acked '✓✓'). Used by the
  // no-downgrade guard in _appendOrUpsert so a duplicate/replay never lowers a
  // bubble's delivery state. 'failed'/unknown map to 0: a stale weaker re-echo
  // can't downgrade a success state, and a genuine delivery confirmation may
  // still upgrade a prior failure. markDelivery stays the authoritative path for
  // terminal 'failed' (set from a relay `error` envelope).
  var _DELIVERY_RANK = { pending: 1, delivered: 2, acked: 3 };

  function _deliveryRank(state) {
    return _DELIVERY_RANK[state] || 0;
  }

  function _trim(value) {
    return String(value == null ? '' : value).trim();
  }

  var _KIND_TIER = { architect: 0, engineer: 1, worker: 2, terminal: 3 };

  function _kindTier(kind) {
    var k = _trim(kind);
    return Object.prototype.hasOwnProperty.call(_KIND_TIER, k) ? _KIND_TIER[k] : 4;
  }

  function _agentListCompare(a, b) {
    var tier = _kindTier(a.kind) - _kindTier(b.kind);
    if (tier) return tier;
    return (b.lastActivityMs || 0) - (a.lastActivityMs || 0);
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
    // Recent-history-on-open (plan §11, scope-extended per TORQUE:578): set once
    // a snapshot envelope is consumed. Drives the "showing new messages" vs
    // "showing recent history" affordance. Stays false when no/empty snapshot,
    // so we degrade gracefully to the replay-only baseline.
    this.snapshotApplied = false;
    this.snapshotRowCount = 0;
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
        kind: '',
      };
      this.conversations[agentId] = conv;
      this.order.push(agentId);
      if (!this.activeAgentId) this.activeAgentId = agentId;
    } else {
      if (conv.kind == null) conv.kind = '';
      if (agentName && conv.agentName === agentId) {
        conv.agentName = agentName;
      }
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
      // Idempotent merge: refresh fields from the newer copy, but NEVER lower
      // the delivery state. A re-echo / replayed snapshot row can carry a weaker
      // delivery_state than the bubble already reached (e.g. a stored 'pending'
      // history row arriving after the live 'acked'); the no-downgrade guard
      // keeps the strongest observed state so the ✓✓/✓ indicator can't flicker
      // backwards on a duplicate. A stronger state still upgrades.
      for (var k in msg) {
        if (!Object.prototype.hasOwnProperty.call(msg, k) || msg[k] === undefined) {
          continue;
        }
        if (k === 'deliveryState'
            && _deliveryRank(msg[k]) < _deliveryRank(existing[k])) {
          continue;
        }
        existing[k] = msg[k];
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

  // Find THIS client's optimistic outbound answer that an inbound user-authored
  // ask_reply echo (§2c) should collapse onto. Matches on agent + reply_to_id +
  // identical body, a different (client-envelope) id, and the optimistic
  // ask_answer shape — so only an echo of our own answer collapses, never a
  // distinct message. Scans newest-first; messages are tail-capped, so bounded.
  RemoteStore.prototype._findOptimisticAnswer = function(conv, echo) {
    for (var i = conv.messages.length - 1; i >= 0; i--) {
      var m = conv.messages[i];
      if (m.sender === 'user' && m.messageType === 'ask_answer'
          && m.replyToId && m.replyToId === echo.replyToId
          && m.id !== echo.id && m.body === echo.body) {
        return m;
      }
    }
    return null;
  };

  // §602: find THIS client's optimistic outbound user message that an inbound
  // user-authored echo (sender_kind="user", payload.idempotency_key present)
  // should collapse onto. Matches on EXACT idempotency_key only — NEVER fuzzy
  // body/sender/recency (that approach was twice-rejected). A different (client
  // -envelope) id distinguishes the optimistic twin from the daemon echo. Scans
  // newest-first; messages are tail-capped, so bounded.
  RemoteStore.prototype._findOptimisticByIdempotencyKey = function(conv, echo) {
    for (var i = conv.messages.length - 1; i >= 0; i--) {
      var m = conv.messages[i];
      if (m.sender === 'user' && m.idempotencyKey
          && m.idempotencyKey === echo.idempotencyKey && m.id !== echo.id) {
        return m;
      }
    }
    return null;
  };

  function _kindFromMessageType(messageType) {
    var m = _trim(messageType);
    if (m === 'ask') return 'ask';
    if (m === 'ask_reply') return 'ask_reply';
    return 'agent_message';
  }

  // Shared ingest core for conversation envelopes (agent_message | ask |
  // ask_reply). Used by BOTH live inbound and snapshot history (opts.history)
  // so snapshot rows render via the exact same path as live messages — there is
  // no separate snapshot renderer. Idempotent by message id; does NOT notify
  // (callers batch the notify). Returns the resulting message, or null.
  RemoteStore.prototype._ingestConversationEnvelope = function(env, opts) {
    opts = opts || {};
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
    // Prefer the stable underlying message_id so a message and its snapshot/live
    // duplicates collapse onto one bubble (de-dupe vs the live stream by id);
    // fall back to the envelope id.
    var id = _trim(payload.message_id) || _trim(env.id);
    if (!id) return null;
    // Derive sender from sender_kind: a user-authored row (e.g. the user's own
    // mirrored ask_reply, sender_kind="user") renders as a user bubble; agent
    // rows render as agent bubbles.
    var sender = _trim(payload.sender_kind) === 'user' ? 'user' : 'agent';
    var messageType = _trim(payload.message_type)
      || (env.kind === 'ask' ? 'ask' : (env.kind === 'ask_reply' ? 'ask_reply' : 'message'));
    var msg = {
      id: id,
      kind: env.kind,
      agentId: agentId,
      sender: sender,
      body: (payload.message == null) ? '' : String(payload.message),
      messageType: messageType,
      blocking: !!payload.blocking,
      threadId: _trim(payload.thread_id),
      replyToId: _trim(payload.reply_to_id),
      idempotencyKey: _trim(payload.idempotency_key),
      createdAtMs: atMs,
      deliveryState: _trim(payload.delivery_state)
        || (sender === 'user' ? 'acked' : 'delivered'),
    };

    // ---- Echoed-own-outbound collapse: a TWO-STAGE layered match -----------
    // The daemon echoes the user's OWN outbound back for multi-client sync
    // (sender_kind="user", a daemon message_id ≠ our optimistic envelope id).
    // We collapse that echo onto its local optimistic twin instead of rendering
    // a second bubble. The two stages are matched in STRICT priority order and
    // are mutually exclusive — an echo collapses via EXACTLY ONE stage:
    //
    //   STAGE 1 (PRIMARY, §602): EXACT idempotency_key match. Every optimistic
    //     outbound minted since :602 carries an idempotency_key (plain messages
    //     AND ask-answers), so this is the path that fires for current clients.
    //   STAGE 2 (FALLBACK, §587/§2c): reply_to_id + body match for an ask_reply
    //     echo. Now only reached when Stage 1 did NOT collapse the echo — i.e. an
    //     ask-answer echo with no matching idempotency_key twin (a pre-:602
    //     optimistic answer, or an echo missing the key). It is a backward-compat
    //     fallback, deliberately NOT load-bearing on the primary path.
    //
    // Stage 1 runs first precisely so an ask-answer echo (which carries BOTH an
    // idempotency_key AND a reply_to_id) collapses via the key path only, never
    // twice. Both stages are EXACT keys — never fuzzy body/sender/recency.

    // STAGE 1 (PRIMARY, §602): collapse onto the optimistic twin by EXACT
    // idempotency_key. Alias the daemon id onto the bubble, lift pending ->
    // delivered, and resolve any referenced ask.
    if (sender === 'user' && msg.idempotencyKey && !conv.messageIndex[id]) {
      var keyTwin = this._findOptimisticByIdempotencyKey(conv, msg);
      if (keyTwin) {
        conv.messageIndex[id] = keyTwin;  // alias daemon id -> existing bubble
        if (keyTwin.deliveryState === 'pending') keyTwin.deliveryState = 'delivered';
        if (msg.replyToId && conv.pendingAsks[msg.replyToId]) {
          delete conv.pendingAsks[msg.replyToId];
        }
        this._touch(agentId, atMs);
        return keyTwin;
      }
    }

    // STAGE 2 (FALLBACK, §587/§2c): an ask_reply echo with no Stage-1 key match.
    // Collapse onto an optimistic answer to the same ask (matched on agent +
    // reply_to_id + identical body): alias the daemon id onto that bubble so a
    // re-echo de-dupes, lift pending -> delivered, resolve the ask, skip the
    // append. Only an echo of our OWN answer collapses; an answer made on another
    // client has no local optimistic twin and renders normally.
    if (env.kind === 'ask_reply' && sender === 'user' && msg.replyToId
        && !conv.messageIndex[id]) {
      var twin = this._findOptimisticAnswer(conv, msg);
      if (twin) {
        conv.messageIndex[id] = twin;  // alias daemon id -> existing bubble
        if (twin.deliveryState === 'pending') twin.deliveryState = 'delivered';
        if (conv.pendingAsks[msg.replyToId]) delete conv.pendingAsks[msg.replyToId];
        this._touch(agentId, atMs);
        return twin;
      }
    }

    // Bump unread only for a genuinely NEW live message (a snapshot row that also
    // arrives live upserts onto its existing id and must not over-count).
    var isNew = !conv.messageIndex[id];
    this._appendOrUpsert(conv, msg);

    // A blocking ask stays ACTIONABLE until answered — for BOTH live and
    // snapshot rows, so reconnecting still lets you answer a still-pending ask
    // raised before you connected.
    if (env.kind === 'ask' && msg.blocking) {
      conv.pendingAsks[msg.id] = msg;
    }
    // An ask_reply (or any reply) referencing a pending ask resolves it —
    // already-answered asks (ask + ask_reply both present) render resolved.
    if (msg.replyToId && conv.pendingAsks[msg.replyToId]) {
      delete conv.pendingAsks[msg.replyToId];
    }
    // History rows never inflate the unread badge; live rows for a non-active
    // agent do — but only on a genuinely new insert, never an upsert.
    if (!opts.history && isNew && agentId !== this.activeAgentId) conv.unread += 1;
    this._touch(agentId, atMs);
    return msg;
  };

  // Ingest a live inbound conversation envelope. Returns the message or null.
  RemoteStore.prototype.ingestInbound = function(env) {
    var msg = this._ingestConversationEnvelope(env, { history: false });
    if (msg) this._notify();
    return msg;
  };

  // Pull the row array out of a snapshot payload. Shipped :578 uses
  // payload.messages; the other keys are accepted defensively.
  function _snapshotRows(payload) {
    var candidates = [payload.messages, payload.rows, payload.items,
      payload.conversation, payload.snapshot, payload.history];
    for (var i = 0; i < candidates.length; i++) {
      if (Array.isArray(candidates[i])) return candidates[i];
    }
    return [];
  }

  // Apply a snapshot envelope: a BATCH of recent USER<->AGENT history rows,
  // consumed on connect. Each row is the SHIPPED :578 shape — a flat live
  // PAYLOAD dict (agent_id/message/message_id/message_type/sender_kind/...) plus
  // a top-level `kind` discriminator (agent_message|ask|ask_reply), NOT a full
  // nested envelope (verified against connector.py _handle_snapshot_request:
  // entry=_direct_message_payload(row, agent_id); entry["kind"]=wire_kind). We
  // wrap each row as the envelope shape the shared ingest core expects
  // (payload = the row) so snapshot rows render via the exact live path and
  // de-dupe against the live stream by message id (store upsert). Applied
  // oldest-first so a still-pending ask + its later reply resolve in order.
  RemoteStore.prototype.ingestSnapshot = function(env) {
    var payload = env && env.payload ? env.payload : {};
    var agents = Array.isArray(payload.agents) ? payload.agents : [];
    for (var a = 0; a < agents.length; a++) {
      var agent = agents[a];
      if (!agent || typeof agent !== 'object') continue;
      var id = _trim(agent.id);
      if (!id) continue;
      var conv = this._ensureConversation(id, _trim(agent.name) || id);
      conv.kind = _trim(agent.kind);
    }
    var rows = _snapshotRows(payload);
    this.snapshotApplied = true;
    if (!rows.length) { this._notify(); return 0; }
    var ordered = rows.slice().filter(function(r) { return r && typeof r === 'object'; })
      .sort(function(a, b) {
        return _createdAtMs(a, env.created_at) - _createdAtMs(b, env.created_at);
      });
    var applied = 0;
    for (var i = 0; i < ordered.length; i++) {
      var row = ordered[i];
      var rowEnv = {
        kind: _trim(row.kind) || _kindFromMessageType(row.message_type),
        id: _trim(row.message_id) || _trim(row.id),
        created_at: row.created_at || env.created_at,
        payload: row,
      };
      if (this._ingestConversationEnvelope(rowEnv, { history: true })) applied += 1;
    }
    this.snapshotRowCount += applied;
    this._notify();
    return applied;
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
      // §602: carry the client-minted idempotency_key on the optimistic bubble
      // so the daemon's user-authored echo can collapse onto it by EXACT key.
      idempotencyKey: _trim(payload.idempotency_key),
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
        kind: conv.kind || '',
        unread: conv.unread,
        pendingAskCount: Object.keys(conv.pendingAsks).length,
        lastBody: last ? last.body : '',
        lastActivityMs: conv.lastActivityMs,
        active: agentId === self.activeAgentId,
      };
    }).sort(_agentListCompare);
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

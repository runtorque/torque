// Remote web UI: end-to-end pipeline test against an EMULATED relay+connector.
//
// This drives the FULL client stack (envelope -> relay_client -> store ->
// render_remote) over a fake socket whose peer faithfully replays the SHIPPED
// connector contract (TORQUE:578 snapshot egress + :534-gated live egress +
// user_message ingress). It is an injected/emulated test, NOT a live relay:
// the relay is TypeScript (needs a tsc build + ws) and the connector is a
// daemon component (needs a live recent_direct_messages provider), so the true
// full-stack UI<->relay<->connector<->daemon round-trip is a MANUAL loopback
// recipe (see ee/frontend/README.md), not automatable in `node --test`.
//
// Proves: (a) snapshot_request->snapshot renders recent history oldest-first;
// (b) snapshot de-dupes vs the live stream by message id; (c) a live ask
// renders an answerable card and the ask_reply (user_message + reply_to_id)
// reaches the agent; (d) user_message reaches the agent and agent_message
// renders back.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function loadBundle() {
  const sandbox = { console };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  ['markdown.js', 'surface.js', 'envelope.js', 'config.js', 'store.js',
    'relay_client.js', 'render_remote.js'].forEach((f) => {
    vm.runInContext(
      fs.readFileSync(path.join(repoRoot, 'ee/frontend/remote/js', f), 'utf8'),
      sandbox, { filename: f });
  });
  return sandbox;
}

// A socket whose peer is a minimal relay+connector emulator. Outbound client
// envelopes are handed to `onClientSend`; the emulator pushes inbound envelopes
// back via `deliver()`.
function emulatedRelay(daemonId) {
  const peerInbound = [];
  let socket = null;
  const api = {
    received: [],          // envelopes the client sent (acks included)
    factory() {
      socket = {
        sent: [],
        send(str) {
          const env = JSON.parse(str);
          api.received.push(env);
          api.onClientSend(env);
        },
        close() {},
      };
      return socket;
    },
    deliver(env) { socket.onmessage({ data: JSON.stringify(env) }); },
    onClientSend() {},     // overridden per test
    daemonId,
  };
  void peerInbound;
  return api;
}

function ready(daemonId) {
  return { v: 1, id: 'ready-1', daemon_id: daemonId,
    source: { kind: 'daemon', id: daemonId },
    target: { kind: 'remote-client', id: 'user', user_id: 'user' },
    kind: 'ready', created_at: new Date().toISOString(),
    payload: { accepted: true, epoch: 1, replayed: 0 } };
}

// A snapshot envelope in the SHIPPED :578 shape (rows = flat payload + kind).
function snapshotEnvelope(daemonId, refId, rows) {
  return { v: 1, id: 'snap-1', daemon_id: daemonId,
    source: { kind: 'daemon', id: daemonId },
    target: { kind: 'remote-client', id: 'user', user_id: 'user' },
    kind: 'snapshot', created_at: new Date().toISOString(),
    payload: { lane: 'user-agent', count: rows.length, limit: 100,
      truncated: false, ref_id: refId, messages: rows } };
}

function liveEnvelope(daemonId, kind, payload) {
  return { v: 1, id: 'env-' + Math.random().toString(36).slice(2), daemon_id: daemonId,
    source: { kind: 'daemon', id: daemonId },
    target: { kind: 'remote-client', id: 'user', user_id: 'user' },
    kind, created_at: new Date().toISOString(), payload };
}

test('e2e: snapshot_request -> snapshot renders recent history oldest-first; de-dupes live', () => {
  const s = loadBundle();
  const relay = emulatedRelay('d');
  const cfg = s.RemoteConfig.parseConfig({ relayBaseUrl: 'http://127.0.0.1:8787', daemonId: 'd', clientId: 'c' });
  const store = new s.RemoteStore({});
  const client = new s.RemoteRelayClient({ config: cfg, store,
    socketFactory: relay.factory, setTimeout: () => 0, clearTimeout: () => {} });

  const now = Date.now();
  const iso = (ms) => new Date(ms).toISOString();
  relay.onClientSend = (env) => {
    if (env.kind === 'snapshot_request') {
      relay.deliver(snapshotEnvelope('d', env.id, [
        // out of order on purpose; connector guarantees oldest-first but we sort
        { kind: 'agent_message', agent_id: 'w', message_id: 'h2', message: 'older-2',
          message_type: 'message', sender_kind: 'worker', sender_name: 'Widget',
          recipient_kind: 'user', created_at: iso(now - 1000) },
        { kind: 'agent_message', agent_id: 'w', message_id: 'h1', message: 'older-1',
          message_type: 'message', sender_kind: 'worker', sender_name: 'Widget',
          recipient_kind: 'user', created_at: iso(now - 2000) },
      ]));
    }
  };

  client.connect();
  relay.deliver(ready('d'));

  const conv = store.conversations['w'];
  assert.ok(conv, 'snapshot created the conversation from traffic');
  assert.equal(conv.messages.map((m) => m.id).join(','), 'h1,h2', 'rendered oldest-first');
  assert.equal(store.snapshotApplied, true);

  // A live agent_message overlapping a snapshot row (same message_id) de-dupes.
  relay.deliver(liveEnvelope('d', 'agent_message',
    { agent_id: 'w', message_id: 'h2', message: 'older-2 (live)', message_type: 'message',
      sender_kind: 'worker', sender_name: 'Widget', recipient_kind: 'user' }));
  assert.equal(store.conversations['w'].messages.length, 2, 'overlap collapsed by id');

  const html = s.RemoteRender.conversationHtml(store.activeConversation(), cfg);
  assert.match(html, /older-1/);
  assert.match(html, /older-2 \(live\)/);
});

test('e2e: live ask renders an answerable card; ask_reply reaches the agent', () => {
  const s = loadBundle();
  const relay = emulatedRelay('d');
  const cfg = s.RemoteConfig.parseConfig({ relayBaseUrl: 'http://h', daemonId: 'd', clientId: 'c' });
  assert.equal(cfg.askEnabled, true, 'ask UI on by default at P6-V1');
  const store = new s.RemoteStore({});
  const client = new s.RemoteRelayClient({ config: cfg, store,
    socketFactory: relay.factory, setTimeout: () => 0, clearTimeout: () => {} });
  relay.onClientSend = (env) => {
    if (env.kind === 'snapshot_request') relay.deliver(snapshotEnvelope('d', env.id, []));
  };
  client.connect();
  relay.deliver(ready('d'));

  // Agent raises a blocking, user-destined ask.
  relay.deliver(liveEnvelope('d', 'ask',
    { agent_id: 'w', message_id: 'ask-42', message: 'Deploy to prod?', message_type: 'ask',
      blocking: true, sender_kind: 'worker', sender_name: 'Widget', recipient_kind: 'user' }));
  assert.equal(store.pendingAsks('w').length, 1, 'pending ask is actionable');
  const card = s.RemoteRender.conversationHtml(store.activeConversation(), cfg);
  assert.match(card, /remote-ask-card/);
  assert.match(card, /data-ask-reply-for="ask-42"/, 'answerable card rendered');

  // User answers -> ask_reply rides on a user_message + reply_to_id, reaching the agent.
  client.sendUserMessage('w', 'yes, ship it', { replyToId: 'ask-42' });
  const answer = relay.received.find((e) => e.kind === 'user_message' && e.payload.reply_to_id === 'ask-42');
  assert.ok(answer, 'ask answer reached the agent as a user_message');
  assert.equal(answer.payload.message, 'yes, ship it');
  assert.equal(answer.payload.agent_id, 'w');
  assert.equal(store.pendingAsks('w').length, 0, 'answering clears the pending ask locally');
});

test('e2e: user_message reaches the agent and agent_message renders back', () => {
  const s = loadBundle();
  const relay = emulatedRelay('d');
  const cfg = s.RemoteConfig.parseConfig({ relayBaseUrl: 'http://h', daemonId: 'd', clientId: 'c' });
  const store = new s.RemoteStore({});
  const client = new s.RemoteRelayClient({ config: cfg, store,
    socketFactory: relay.factory, setTimeout: () => 0, clearTimeout: () => {} });
  relay.onClientSend = (env) => {
    if (env.kind === 'snapshot_request') { relay.deliver(snapshotEnvelope('d', env.id, [])); return; }
    if (env.kind === 'user_message' && !env.payload.reply_to_id) {
      // Agent replies.
      relay.deliver(liveEnvelope('d', 'agent_message',
        { agent_id: 'w', message_id: 'reply-1', message: 'got it: ' + env.payload.message,
          message_type: 'message', sender_kind: 'worker', sender_name: 'Widget',
          recipient_kind: 'user' }));
    }
  };
  client.connect();
  relay.deliver(ready('d'));
  // Need an active conversation to send into; the agent must have messaged first.
  relay.deliver(liveEnvelope('d', 'agent_message',
    { agent_id: 'w', message_id: 'a0', message: 'hi', message_type: 'message',
      sender_kind: 'worker', sender_name: 'Widget', recipient_kind: 'user' }));

  client.sendUserMessage('w', 'status?', {});
  const sentMsg = relay.received.find((e) => e.kind === 'user_message' && e.payload.message === 'status?');
  assert.ok(sentMsg, 'user_message reached the agent');

  const html = s.RemoteRender.conversationHtml(store.activeConversation(), cfg);
  assert.match(html, /got it: status\?/, 'agent_message rendered back');
});

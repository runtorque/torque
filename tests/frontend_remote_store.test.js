// Remote web UI: conversation store — ingest, pending asks, delivery state,
// tail cap, idempotent upsert, and snapshot-on-connect (TORQUE:578).

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function loadStore(opts) {
  const sandbox = { console };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(
    fs.readFileSync(path.join(repoRoot, 'ee/frontend/remote/js/store.js'), 'utf8'),
    sandbox, { filename: 'store.js' });
  return new sandbox.RemoteStore(opts || {});
}

function agentMessage(id, agentId, body, extra) {
  return Object.assign({
    v: 1, id, daemon_id: 'd',
    source: { kind: 'daemon', id: 'd' },
    target: { kind: 'remote-client', id: 'user', user_id: 'user' },
    kind: 'agent_message', created_at: new Date().toISOString(),
    payload: Object.assign({ agent_id: agentId, sender_kind: 'worker',
      sender_name: agentId, message: body, message_id: id }, extra || {}),
  });
}

test('ingestInbound creates per-agent conversations from traffic', () => {
  const store = loadStore();
  store.ingestInbound(agentMessage('m1', 'worker-a', 'hi from a'));
  store.ingestInbound(agentMessage('m2', 'worker-b', 'hi from b'));
  const agents = store.agentList();
  assert.equal(agents.length, 2);
  const a = store.conversations['worker-a'];
  assert.equal(a.messages.length, 1);
  assert.equal(a.messages[0].sender, 'agent');
  assert.equal(a.messages[0].body, 'hi from a');
});

test('blocking ask sets pendingAsks; an answer clears it', () => {
  const store = loadStore();
  const ask = agentMessage('ask-1', 'w', 'approve?', { message_type: 'ask', blocking: true });
  ask.kind = 'ask';
  store.ingestInbound(ask);
  assert.equal(store.pendingAsks('w').length, 1);
  // User answers via an outbound user_message carrying reply_to_id.
  store.recordOutbound({
    id: 'u1', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', message: 'yes', reply_to_id: 'ask-1' },
  });
  assert.equal(store.pendingAsks('w').length, 0, 'answering clears the pending ask');
});

test('outbound delivery state advances on ack and error', () => {
  const store = loadStore();
  store.recordOutbound({ id: 'u9', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', message: 'hello' } });
  assert.equal(store.conversations['w'].messageIndex['u9'].deliveryState, 'pending');
  store.markDelivery('u9', 'acked');
  assert.equal(store.conversations['w'].messageIndex['u9'].deliveryState, 'acked');
  store.markDelivery('u9', 'failed', 'boom');
  assert.equal(store.conversations['w'].messageIndex['u9'].deliveryState, 'failed');
});

test('per-conversation history is tail-capped', () => {
  const store = loadStore({ tailCap: 3 });
  for (let i = 0; i < 5; i++) store.ingestInbound(agentMessage('m' + i, 'w', 'body ' + i));
  const conv = store.conversations['w'];
  assert.equal(conv.messages.length, 3);
  assert.equal(conv.messages[0].body, 'body 2', 'oldest dropped');
  assert.equal(conv.messageIndex['m0'], undefined, 'dropped id evicted from index');
});

test('duplicate message id upserts (no duplicate bubble)', () => {
  const store = loadStore();
  store.ingestInbound(agentMessage('dup', 'w', 'first'));
  store.ingestInbound(agentMessage('dup', 'w', 'first-edited'));
  assert.equal(store.conversations['w'].messages.length, 1);
  assert.equal(store.conversations['w'].messages[0].body, 'first-edited');
});

test('snapshot consumes the real :578 row shape (flat payload + kind) oldest-first', () => {
  const store = loadStore();
  const now = Date.now();
  const iso = (ms) => new Date(ms).toISOString();
  // Shipped :578 row shape: a FLAT live payload dict + a top-level `kind`
  // discriminator (NOT a nested envelope). Both sides appear: an agent_message
  // and the user's own mirrored ask_reply (sender_kind="user"). Out of order on
  // purpose to prove oldest-first ordering.
  const applied = store.ingestSnapshot({
    kind: 'snapshot', created_at: iso(now),
    payload: { lane: 'user-agent', count: 2, limit: 100, truncated: false, ref_id: 'req-1',
      messages: [
        { kind: 'agent_message', agent_id: 'w', message_id: 's2', message: 'second',
          message_type: 'message', sender_kind: 'worker', sender_name: 'w',
          recipient_kind: 'user', created_at: iso(now - 1000) },
        { kind: 'ask_reply', agent_id: 'w', message_id: 's1', message: 'my earlier answer',
          message_type: 'ask_reply', sender_kind: 'user', reply_to_id: 'ask-0',
          created_at: iso(now - 2000) },
      ] },
  });
  assert.equal(applied, 2, 'both rows consumed (real shape, not skipped)');
  assert.equal(store.snapshotApplied, true);
  const conv = store.conversations['w'];
  assert.equal(conv.messages.length, 2);
  assert.equal(conv.messages[0].id, 's1', 'sorted oldest-first');
  assert.equal(conv.messages[0].sender, 'user', 'user ask_reply derives sender from sender_kind');
  assert.equal(conv.messages[1].sender, 'agent');
  assert.equal(conv.unread, 0, 'snapshot history does not bump unread');

  // A live message overlapping a snapshot row upserts onto the same bubble
  // (de-dupe vs live stream by message id).
  store.ingestInbound(agentMessage('s2', 'w', 'second (live edit)'));
  assert.equal(store.conversations['w'].messages.length, 2, 'overlap de-dupes via upsert');
  assert.equal(store.conversations['w'].messageIndex['s2'].body, 'second (live edit)');
});

test('snapshot unanswered blocking ask is actionable but does not bump unread', () => {
  const store = loadStore();
  const now = Date.now();
  const iso = (ms) => new Date(ms).toISOString();
  store.ingestSnapshot({
    kind: 'snapshot', created_at: iso(now),
    payload: { messages: [
      { kind: 'ask', agent_id: 'w', message_id: 'ask-9', message: 'approve deploy?',
        message_type: 'ask', blocking: true, sender_kind: 'worker', recipient_kind: 'user',
        created_at: iso(now - 5000) },
    ] },
  });
  assert.equal(store.pendingAsks('w').length, 1,
    'a still-pending snapshot ask remains answerable after reconnect');
  assert.equal(store.conversations['w'].unread, 0, 'snapshot ask does not bump unread');
});

test('snapshot ask + its ask_reply both present render resolved (not pending)', () => {
  const store = loadStore();
  const now = Date.now();
  const iso = (ms) => new Date(ms).toISOString();
  store.ingestSnapshot({
    kind: 'snapshot', created_at: iso(now),
    payload: { messages: [
      { kind: 'ask', agent_id: 'w', message_id: 'ask-7', message: 'ok?',
        message_type: 'ask', blocking: true, sender_kind: 'worker', recipient_kind: 'user',
        created_at: iso(now - 3000) },
      { kind: 'ask_reply', agent_id: 'w', message_id: 'rep-7', message: 'yes',
        message_type: 'ask_reply', sender_kind: 'user', reply_to_id: 'ask-7',
        created_at: iso(now - 2000) },
    ] },
  });
  assert.equal(store.pendingAsks('w').length, 0,
    'already-answered ask (reply also in snapshot) is resolved');
  assert.equal(store.conversations['w'].messages.length, 2);
});

test('empty/absent snapshot degrades gracefully (replay-only baseline)', () => {
  const store = loadStore();
  store.ingestSnapshot({ kind: 'snapshot', created_at: new Date().toISOString(), payload: {} });
  assert.equal(store.snapshotApplied, true);
  assert.equal(store.snapshotRowCount, 0);
  assert.equal(store.agentList().length, 0);
});

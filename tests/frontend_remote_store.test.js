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

test('§2c: echoed user ask_reply collapses onto its optimistic answer (one bubble)', () => {
  const store = loadStore();
  // 1) Agent raises a blocking ask.
  const ask = agentMessage('ask-0', 'w', 'approve deploy?', { message_type: 'ask', blocking: true });
  ask.kind = 'ask';
  store.ingestInbound(ask);
  assert.equal(store.pendingAsks('w').length, 1);
  // 2) User answers — optimistic outbound keyed by the CLIENT envelope id.
  store.recordOutbound({
    id: 'env-reply-client', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', message: 'ship it', reply_to_id: 'ask-0' },
  });
  assert.equal(store.conversations['w'].messages.length, 2);
  assert.equal(store.pendingAsks('w').length, 0, 'optimistic answer clears the ask');
  // 3) Daemon echoes the answer back as an ask_reply with a DIFFERENT message_id
  //    (sender_kind=user, multi-client sync). Pre-fix this appended a 2nd bubble.
  const echo = agentMessage('reply-daemon-1', 'w', 'ship it', {
    message_type: 'ask_reply', sender_kind: 'user', reply_to_id: 'ask-0' });
  echo.kind = 'ask_reply';
  store.ingestInbound(echo);

  const conv = store.conversations['w'];
  assert.equal(conv.messages.length, 2, 'echo collapses — exactly ONE answer bubble');
  const answers = conv.messages.filter((m) => m.sender === 'user' && m.messageType === 'ask_answer');
  assert.equal(answers.length, 1, 'a single answer bubble remains');
  assert.equal(answers[0].id, 'env-reply-client', 'optimistic bubble id is preserved (DOM-stable)');
  assert.equal(answers[0].deliveryState, 'delivered', 'echo upgrades pending -> delivered');
  assert.equal(store.pendingAsks('w').length, 0, 'ask stays resolved');
  // A re-echo (same daemon id) de-dupes onto the aliased bubble — still one.
  store.ingestInbound(echo);
  assert.equal(conv.messages.length, 2, 're-echo de-dupes via the aliased daemon id');
});

test('§2c: an ask_reply with no local optimistic twin renders normally (multi-client)', () => {
  const store = loadStore();
  // No optimistic outbound on this client — e.g. the answer was made elsewhere.
  const echo = agentMessage('reply-daemon-2', 'w', 'answer from another device', {
    message_type: 'ask_reply', sender_kind: 'user', reply_to_id: 'ask-x' });
  echo.kind = 'ask_reply';
  store.ingestInbound(echo);
  const conv = store.conversations['w'];
  assert.equal(conv.messages.length, 1, 'with no twin, the echo renders as its own bubble');
  assert.equal(conv.messages[0].sender, 'user');
});

test('§2c NEGATIVE: distinct asks (different reply_to_ids) never cross-collapse', () => {
  const store = loadStore();
  // Two distinct asks, two distinct optimistic answers.
  store.recordOutbound({ id: 'env-a', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', message: 'yes', reply_to_id: 'ask-A' } });
  store.recordOutbound({ id: 'env-b', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', message: 'yes', reply_to_id: 'ask-B' } });
  assert.equal(store.conversations['w'].messages.length, 2);
  // Echo for ask-A only. Body 'yes' coincidentally matches BOTH answers, so a
  // body-only match would over-collapse — the strict reply_to_id key must not.
  const echoA = agentMessage('reply-A', 'w', 'yes', {
    message_type: 'ask_reply', sender_kind: 'user', reply_to_id: 'ask-A' });
  echoA.kind = 'ask_reply';
  store.ingestInbound(echoA);
  const conv = store.conversations['w'];
  assert.equal(conv.messages.length, 2, 'echo-A collapses onto answer-A; answer-B untouched');
  assert.equal(conv.messageIndex['reply-A'].replyToId, 'ask-A', 'echo aliased onto the A bubble');
  assert.equal(conv.messageIndex['reply-A'].id, 'env-a', 'collapsed onto answer-A, not answer-B');
  // The B answer is still its own pending bubble, NOT swallowed.
  assert.equal(conv.messageIndex['env-b'].deliveryState, 'pending');
});

test('§2c NEGATIVE: an agent_message never collapses onto an optimistic answer', () => {
  const store = loadStore();
  store.recordOutbound({ id: 'env-x', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', message: 'do the thing', reply_to_id: 'ask-z' } });
  // An ordinary agent reply with the SAME body — different kind/sender, must append.
  store.ingestInbound(agentMessage('m-agent', 'w', 'do the thing',
    { reply_to_id: 'ask-z' }));
  assert.equal(store.conversations['w'].messages.length, 2,
    'agent_message is a distinct bubble, never collapsed onto a user answer');
});

test('§602: echoed user_message collapses onto its optimistic by idempotency_key (one bubble)', () => {
  const store = loadStore();
  // 1) User sends a plain message; optimistic outbound carries the minted key.
  store.recordOutbound({
    id: 'env-client-1', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', message: 'deploy now', idempotency_key: 'idem-abc' },
  });
  assert.equal(store.conversations['w'].messages.length, 1);
  // 2) Daemon echoes the user-authored message back (multi-client sync) reusing
  //    the agent_message shape: sender_kind=user, a DIFFERENT daemon message_id,
  //    and the same idempotency_key.
  store.ingestInbound(agentMessage('msg-daemon-1', 'w', 'deploy now', {
    sender_kind: 'user', idempotency_key: 'idem-abc' }));
  const conv = store.conversations['w'];
  assert.equal(conv.messages.length, 1, 'echo collapses — exactly ONE user bubble');
  assert.equal(conv.messages[0].id, 'env-client-1', 'optimistic bubble id preserved (DOM-stable)');
  assert.equal(conv.messages[0].deliveryState, 'delivered', 'echo upgrades pending -> delivered');
  assert.equal(conv.messageIndex['msg-daemon-1'].id, 'env-client-1', 'daemon id aliased onto the bubble');
  // A re-echo (same daemon id) de-dupes onto the aliased bubble — still one.
  store.ingestInbound(agentMessage('msg-daemon-1', 'w', 'deploy now', {
    sender_kind: 'user', idempotency_key: 'idem-abc' }));
  assert.equal(conv.messages.length, 1, 're-echo de-dupes via the aliased daemon id');
});

test('no-downgrade guard: a weaker re-echo never lowers an already-stronger delivery state', () => {
  const store = loadStore();
  // Optimistic send carrying the minted idempotency_key.
  store.recordOutbound({ id: 'env-1', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', message: 'go', idempotency_key: 'k1' } });
  // Daemon echo collapses onto the optimistic bubble (pending -> delivered) and
  // aliases the daemon message_id onto it.
  store.ingestInbound(agentMessage('msg-1', 'w', 'go', {
    sender_kind: 'user', idempotency_key: 'k1' }));
  const conv = store.conversations['w'];
  assert.equal(conv.messages[0].deliveryState, 'delivered');
  // An explicit ack lifts it to the strongest state (acked, ✓✓).
  store.markDelivery('env-1', 'acked');
  assert.equal(conv.messages[0].deliveryState, 'acked');
  // A re-echo / replayed snapshot row for the SAME daemon id arrives carrying a
  // WEAKER delivery_state (e.g. the 'pending' stored at send time). It upserts
  // onto the aliased bubble — the no-downgrade guard must keep 'acked'.
  store.ingestInbound(agentMessage('msg-1', 'w', 'go', {
    sender_kind: 'user', idempotency_key: 'k1', delivery_state: 'pending' }));
  assert.equal(conv.messages.length, 1, 're-echo de-dupes onto one bubble');
  assert.equal(conv.messages[0].deliveryState, 'acked',
    'no-downgrade guard: a weaker re-echo never lowers acked');
});

test('no-downgrade guard: a STRONGER re-echo still upgrades the delivery state', () => {
  const store = loadStore();
  store.recordOutbound({ id: 'env-2', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', message: 'hi', idempotency_key: 'k2' } });
  // First echo collapses pending -> delivered (✓).
  store.ingestInbound(agentMessage('msg-2', 'w', 'hi', {
    sender_kind: 'user', idempotency_key: 'k2' }));
  const conv = store.conversations['w'];
  assert.equal(conv.messages[0].deliveryState, 'delivered');
  // A re-echo carrying the stronger 'acked' (✓✓) upgrades the bubble — the guard
  // blocks only downgrades, never upgrades.
  store.ingestInbound(agentMessage('msg-2', 'w', 'hi', {
    sender_kind: 'user', idempotency_key: 'k2', delivery_state: 'acked' }));
  assert.equal(conv.messages.length, 1);
  assert.equal(conv.messages[0].deliveryState, 'acked', 'stronger state still upgrades');
});

test('§602 NEGATIVE: distinct idempotency_keys never collapse', () => {
  const store = loadStore();
  store.recordOutbound({ id: 'env-1', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', message: 'same text', idempotency_key: 'idem-1' } });
  // Echo carries a DIFFERENT key — must NOT collapse despite identical text.
  store.ingestInbound(agentMessage('msg-2', 'w', 'same text', {
    sender_kind: 'user', idempotency_key: 'idem-2' }));
  assert.equal(store.conversations['w'].messages.length, 2,
    'a different key is a distinct message, never collapsed');
});

test('§602 NEGATIVE: two identical-TEXT user messages with distinct keys never collapse (no fuzzy)', () => {
  const store = loadStore();
  store.recordOutbound({ id: 'env-a', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', message: 'ok', idempotency_key: 'k-a' } });
  store.recordOutbound({ id: 'env-b', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', message: 'ok', idempotency_key: 'k-b' } });
  assert.equal(store.conversations['w'].messages.length, 2);
  // Echo for k-a only — body 'ok' matches BOTH, but EXACT-key match collapses one.
  store.ingestInbound(agentMessage('msg-a', 'w', 'ok', {
    sender_kind: 'user', idempotency_key: 'k-a' }));
  const conv = store.conversations['w'];
  assert.equal(conv.messages.length, 2, 'echo-a collapses onto env-a; env-b untouched');
  assert.equal(conv.messageIndex['msg-a'].id, 'env-a', 'collapsed onto the matching key, not the twin');
  assert.equal(conv.messageIndex['env-b'].deliveryState, 'pending', 'the other bubble is untouched');
});

test('§602: a LOCALLY-originated user message (no optimistic twin) renders fresh', () => {
  const store = loadStore();
  // Typed in the LOCAL Torque UI, not via this remote client — it still echoes
  // with sender_kind=user + an idempotency_key, but there is NO matching
  // optimistic twin on this client, so it renders as its own fresh bubble.
  store.ingestInbound(agentMessage('msg-local', 'w', 'typed locally', {
    sender_kind: 'user', idempotency_key: 'idem-local' }));
  const conv = store.conversations['w'];
  assert.equal(conv.messages.length, 1, 'no twin -> renders fresh');
  assert.equal(conv.messages[0].sender, 'user');
  assert.equal(conv.messages[0].id, 'msg-local', 'keeps its own daemon id');
});

test('§602 x §587: an ask-answer echo (reply_to_id + idempotency_key) collapses exactly ONCE', () => {
  const store = loadStore();
  // Agent raises a blocking ask.
  const ask = agentMessage('ask-9', 'w', 'approve?', { message_type: 'ask', blocking: true });
  ask.kind = 'ask';
  store.ingestInbound(ask);
  assert.equal(store.pendingAsks('w').length, 1);
  // User answers: optimistic outbound carries BOTH reply_to_id AND idempotency_key.
  store.recordOutbound({
    id: 'env-answer', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', message: 'yes', reply_to_id: 'ask-9', idempotency_key: 'idem-ans' },
  });
  assert.equal(store.conversations['w'].messages.length, 2);
  assert.equal(store.pendingAsks('w').length, 0, 'optimistic answer clears the ask');
  // Daemon echoes the answer back carrying BOTH keys — it could match the §587
  // (reply_to_id + body) path AND the §602 idempotency_key path. It must collapse
  // via EXACTLY ONE path (the EXACT-key path is tried first), leaving one bubble.
  const echo = agentMessage('reply-daemon-9', 'w', 'yes', {
    message_type: 'ask_reply', sender_kind: 'user', reply_to_id: 'ask-9', idempotency_key: 'idem-ans' });
  echo.kind = 'ask_reply';
  store.ingestInbound(echo);
  const conv = store.conversations['w'];
  assert.equal(conv.messages.length, 2, 'echo collapses exactly once — no double-collapse, no 2nd bubble');
  const answers = conv.messages.filter((m) => m.sender === 'user' && m.messageType === 'ask_answer');
  assert.equal(answers.length, 1, 'a single answer bubble remains');
  assert.equal(answers[0].id, 'env-answer', 'collapsed onto the optimistic answer');
  assert.equal(answers[0].deliveryState, 'delivered', 'pending -> delivered');
  assert.equal(conv.messageIndex['reply-daemon-9'].id, 'env-answer', 'daemon id aliased onto the one bubble');
  assert.equal(store.pendingAsks('w').length, 0, 'ask stays resolved');
});

test('§602 SNAPSHOT: a user-authored row collapses onto an optimistic twin by idempotency_key', () => {
  const store = loadStore();
  const now = Date.now();
  const iso = (ms) => new Date(ms).toISOString();
  // An optimistic outbound exists (the user sent it this session, pre-reconnect).
  store.recordOutbound({ id: 'env-snap', created_at: iso(now - 1000),
    payload: { agent_id: 'w', message: 'hello from remote', idempotency_key: 'idem-snap' } });
  // Reconnect snapshot now carries the user-authored row (both directions are in
  // the :578 snapshot) with the SAME idempotency_key + a daemon message_id.
  store.ingestSnapshot({
    kind: 'snapshot', created_at: iso(now),
    payload: { messages: [
      { kind: 'agent_message', agent_id: 'w', message_id: 'msg-snap-daemon',
        message: 'hello from remote', message_type: 'message', sender_kind: 'user',
        recipient_kind: 'worker', idempotency_key: 'idem-snap', created_at: iso(now - 1000) },
    ] },
  });
  const conv = store.conversations['w'];
  assert.equal(conv.messages.length, 1, 'snapshot row collapses onto the optimistic twin');
  assert.equal(conv.messages[0].id, 'env-snap', 'optimistic bubble preserved');
  assert.equal(conv.messageIndex['msg-snap-daemon'].id, 'env-snap', 'daemon id aliased onto it');
});

test('§602 SNAPSHOT: a fresh-connect user-authored row (no twin) renders fresh', () => {
  const store = loadStore();
  const now = Date.now();
  const iso = (ms) => new Date(ms).toISOString();
  // Fresh connect: no optimistic twin exists. The user-authored snapshot row
  // (e.g. typed locally, or from a prior session) renders as its own bubble.
  store.ingestSnapshot({
    kind: 'snapshot', created_at: iso(now),
    payload: { messages: [
      { kind: 'agent_message', agent_id: 'w', message_id: 'msg-fresh',
        message: 'earlier message', message_type: 'message', sender_kind: 'user',
        recipient_kind: 'worker', idempotency_key: 'idem-fresh', created_at: iso(now - 1000) },
    ] },
  });
  const conv = store.conversations['w'];
  assert.equal(conv.messages.length, 1, 'no twin -> renders fresh');
  assert.equal(conv.messages[0].id, 'msg-fresh', 'keeps its own daemon id');
  assert.equal(conv.messages[0].sender, 'user');
});

test('§2c NEGATIVE: same answer text to a DIFFERENT agent does not collapse', () => {
  const store = loadStore();
  store.recordOutbound({ id: 'env-w', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', message: 'approved', reply_to_id: 'ask-1' } });
  // Echo carries the same reply_to_id + body but targets a DIFFERENT agent — it
  // lands in a separate conversation, so there is no twin to collapse onto.
  const echo = agentMessage('reply-other', 'other', 'approved', {
    message_type: 'ask_reply', sender_kind: 'user', reply_to_id: 'ask-1' });
  echo.kind = 'ask_reply';
  store.ingestInbound(echo);
  assert.equal(store.conversations['w'].messages.length, 1, 'agent w answer untouched');
  assert.equal(store.conversations['other'].messages.length, 1,
    'cross-agent echo renders in its own conversation, not collapsed');
});

test('snapshot row that also arrives live does not over-count unread (item 2)', () => {
  const store = loadStore();
  const now = Date.now();
  const iso = (ms) => new Date(ms).toISOString();
  // Two agents so the live agent is NOT active (active is the first-seen 'w').
  store.ingestSnapshot({
    kind: 'snapshot', created_at: iso(now),
    payload: { messages: [
      { kind: 'agent_message', agent_id: 'w', message_id: 'a1', message: 'hello',
        message_type: 'message', sender_kind: 'worker', sender_name: 'w',
        recipient_kind: 'user', created_at: iso(now - 1000) },
      { kind: 'agent_message', agent_id: 'other', message_id: 'b1', message: 'hi',
        message_type: 'message', sender_kind: 'worker', sender_name: 'other',
        recipient_kind: 'user', created_at: iso(now - 900) },
    ] },
  });
  assert.equal(store.activeAgentId, 'w');
  assert.equal(store.conversations['other'].unread, 0, 'snapshot history never bumps unread');
  // The SAME message 'b1' now arrives live (snapshot↔live overlap). It upserts
  // onto the existing id and must NOT bump unread.
  store.ingestInbound(agentMessage('b1', 'other', 'hi'));
  assert.equal(store.conversations['other'].messages.length, 1, 'upsert, not a 2nd bubble');
  assert.equal(store.conversations['other'].unread, 0, 'overlapping live row does not over-count');
  // A genuinely new live message for the non-active agent still bumps unread.
  store.ingestInbound(agentMessage('b2', 'other', 'follow-up'));
  assert.equal(store.conversations['other'].unread, 1, 'a new id still counts');
});

test('empty/absent snapshot degrades gracefully (replay-only baseline)', () => {
  const store = loadStore();
  store.ingestSnapshot({ kind: 'snapshot', created_at: new Date().toISOString(), payload: {} });
  assert.equal(store.snapshotApplied, true);
  assert.equal(store.snapshotRowCount, 0);
  assert.equal(store.agentList().length, 0);
});

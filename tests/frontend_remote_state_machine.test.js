// Remote web UI: WS reconnect/re-auth state machine + outbound queue flush.
// Verifies the approved close-code policy (plan §0.1):
//   4003 -> terminal reauth_required (no reconnect, no tight-retry of dead token)
//   every other close -> reconnect-with-backoff using the SAME session.

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
  ['envelope.js', 'config.js', 'store.js', 'relay_client.js'].forEach((f) => {
    vm.runInContext(
      fs.readFileSync(path.join(repoRoot, 'ee/frontend/remote/js', f), 'utf8'),
      sandbox, { filename: f });
  });
  return sandbox;
}

function memorySocket() {
  return { sent: [], closed: null,
    send(str) { this.sent.push(JSON.parse(str)); },
    close(code, reason) { this.closed = { code, reason }; } };
}

function ready(daemonId, epoch) {
  return { v: 1, id: 'ready-' + Math.random(), daemon_id: daemonId,
    source: { kind: 'daemon', id: daemonId },
    target: { kind: 'remote-client', id: 'user', user_id: 'user' },
    kind: 'ready', created_at: new Date().toISOString(),
    payload: { accepted: true, epoch: epoch == null ? 1 : epoch } };
}

function makeClient(s, sockets) {
  const cfg = s.RemoteConfig.parseConfig({
    relayBaseUrl: 'http://127.0.0.1:8787', daemonId: 'd', clientId: 'c' });
  const store = new s.RemoteStore({});
  const timers = [];
  const statuses = [];
  const client = new s.RemoteRelayClient({
    config: cfg, store,
    socketFactory: () => { const sk = memorySocket(); sockets.push(sk); return sk; },
    setTimeout: (fn, delay) => { timers.push({ fn, delay }); return timers.length; },
    clearTimeout: () => {},
    onStatus: (st) => statuses.push(st),
  });
  return { client, store, timers, statuses, S: s.RemoteRelayClient.STATUS };
}

test('queued outbound flushes only after ready', () => {
  const s = loadBundle();
  const sockets = [];
  const { client, S } = makeClient(s, sockets);
  client.connect();
  assert.equal(client.status, S.CONNECTING);
  client.sendUserMessage('w', 'queued before ready');
  assert.equal(sockets[0].sent.filter((e) => e.kind === 'user_message').length, 0,
    'not sent while connecting');
  sockets[0].onmessage({ data: JSON.stringify(ready('d')) });
  assert.equal(client.status, S.READY);
  const sent = sockets[0].sent.filter((e) => e.kind === 'user_message');
  assert.equal(sent.length, 1, 'flushed on ready');
  assert.equal(sent[0].payload.message, 'queued before ready');
});

test('§602: sendUserMessage mints an idempotency_key and records it on the optimistic bubble', () => {
  const s = loadBundle();
  const sockets = [];
  const { client, store } = makeClient(s, sockets);
  client.connect();
  sockets[0].onmessage({ data: JSON.stringify(ready('d')) });
  const env = client.sendUserMessage('w', 'deploy now');
  const key = env.payload.idempotency_key;
  assert.ok(key, 'a per-message idempotency_key is minted onto the outbound payload');
  const sent = sockets[0].sent.filter((e) => e.kind === 'user_message');
  assert.equal(sent.length, 1);
  assert.equal(sent[0].payload.idempotency_key, key, 'the minted key is sent on the wire');
  const optimistic = store.conversations['w'].messageIndex[env.id];
  assert.equal(optimistic.idempotencyKey, key, 'optimistic bubble records the key for echo dedupe');
  // A second send mints a DISTINCT key (per-message, never reused).
  const env2 = client.sendUserMessage('w', 'deploy now');
  assert.notEqual(env2.payload.idempotency_key, key, 'each message gets a distinct key');
});

test('snapshot_request is sent on ready (recent-history-on-connect)', () => {
  const s = loadBundle();
  const sockets = [];
  const { client } = makeClient(s, sockets);
  client.connect();
  assert.equal(sockets[0].sent.filter((e) => e.kind === 'snapshot_request').length, 0,
    'not requested before ready');
  sockets[0].onmessage({ data: JSON.stringify(ready('d')) });
  const reqs = sockets[0].sent.filter((e) => e.kind === 'snapshot_request');
  assert.equal(reqs.length, 1, 'exactly one snapshot_request on ready');
  assert.equal(reqs[0].source.kind, 'remote-client');
  assert.equal(reqs[0].target.kind, 'daemon');
  assert.equal(reqs[0].target.id, 'd');
  assert.equal(reqs[0].v, 1);
  assert.ok(reqs[0].id, 'has a fresh envelope id');
});

test('snapshot_request is re-sent after a reconnect ready', () => {
  const s = loadBundle();
  const sockets = [];
  const { client } = makeClient(s, sockets);
  client.connect();
  sockets[0].onmessage({ data: JSON.stringify(ready('d')) });
  sockets[0].onclose({ code: 1006 });
  client.connect(); // simulate scheduled reconnect firing
  sockets[1].onmessage({ data: JSON.stringify(ready('d')) });
  assert.equal(sockets[1].sent.filter((e) => e.kind === 'snapshot_request').length, 1,
    're-requests history on the fresh connection (dedupe by id handles overlap)');
});

test('close 4003 -> terminal reauth_required, no reconnect scheduled', () => {
  const s = loadBundle();
  const sockets = [];
  const { client, timers, S } = makeClient(s, sockets);
  client.connect();
  sockets[0].onmessage({ data: JSON.stringify(ready('d')) });
  sockets[0].onclose({ code: 4003 });
  assert.equal(client.status, S.REAUTH_REQUIRED);
  assert.equal(timers.length, 0, 'must NOT schedule a reconnect on a dead session');
  // connect() is a no-op once reauth is required (no tight-retry of dead token).
  client.connect();
  assert.equal(sockets.length, 1, 'no new socket opened after 4003');
});

test('non-4003 closes reconnect with backoff using the same session', () => {
  const s = loadBundle();
  for (const code of [4001, 4000, 1006, 1011]) {
    const sockets = [];
    const { client, timers, S } = makeClient(s, sockets);
    client.connect();
    sockets[0].onmessage({ data: JSON.stringify(ready('d')) });
    sockets[0].onclose({ code });
    assert.equal(client.status, S.RECONNECTING, 'code ' + code + ' is reconnectable');
    assert.equal(timers.length, 1, 'code ' + code + ' schedules a reconnect');
    assert.ok(timers[0].delay >= 0);
  }
});

test('backoff attempts grow then reset on a clean ready', () => {
  const s = loadBundle();
  const sockets = [];
  const { client } = makeClient(s, sockets);
  client.connect();
  sockets[0].onclose({ code: 1006 });
  assert.equal(client._reconnectAttempts, 1);
  // simulate the scheduled reconnect firing
  client.connect();
  sockets[1].onclose({ code: 1006 });
  assert.equal(client._reconnectAttempts, 2);
  client.connect();
  sockets[2].onmessage({ data: JSON.stringify(ready('d')) });
  assert.equal(client._reconnectAttempts, 0, 'reset on ready');
});

// Remote web UI: at-least-once de-dupe — duplicate inbound envelopes (live or
// reconnect replay) are dropped exactly once and acked exactly once.

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
  return {
    sent: [],
    closed: null,
    send(str) { this.sent.push(JSON.parse(str)); },
    close(code, reason) { this.closed = { code, reason }; },
  };
}

function readyEnvelope(daemonId) {
  return {
    v: 1, id: 'ready-1', daemon_id: daemonId,
    source: { kind: 'daemon', id: daemonId },
    target: { kind: 'remote-client', id: 'user', user_id: 'user' },
    kind: 'ready', created_at: new Date().toISOString(),
    payload: { accepted: true, epoch: 3, replayed: 0 },
  };
}

function agentMessage(id, daemonId) {
  return {
    v: 1, id, daemon_id: daemonId,
    source: { kind: 'daemon', id: daemonId },
    target: { kind: 'remote-client', id: 'user', user_id: 'user' },
    kind: 'agent_message', created_at: new Date().toISOString(),
    payload: { agent_id: 'w', sender_kind: 'worker', sender_name: 'w', message: 'hi', message_id: id },
  };
}

test('duplicate DATA envelope is ingested once but RE-ACKED (stops replay)', () => {
  const s = loadBundle();
  const cfg = s.RemoteConfig.parseConfig({ relayBaseUrl: 'http://127.0.0.1:8787', daemonId: 'd', clientId: 'c' });
  const store = new s.RemoteStore({});
  const socket = memorySocket();
  const client = new s.RemoteRelayClient({
    config: cfg, store, socketFactory: () => socket,
    setTimeout: () => 0, clearTimeout: () => {},
  });
  client.connect();
  socket.onmessage({ data: JSON.stringify(readyEnvelope('d')) });

  const msg = agentMessage('msg-7', 'd');
  socket.onmessage({ data: JSON.stringify(msg) });
  socket.onmessage({ data: JSON.stringify(msg) }); // duplicate (lost-ack replay)

  assert.equal(store.conversations['w'].messages.length, 1, 'dispatched/ingested once');
  const acks = socket.sent.filter((e) => e.kind === 'ack' && e.payload.ack_id === 'msg-7');
  assert.equal(acks.length, 2,
    're-acked on the duplicate so the relay stops re-replaying (P6.1 #1)');
});

test('duplicate control envelope (ping) is dropped, not re-acked', () => {
  const s = loadBundle();
  const cfg = s.RemoteConfig.parseConfig({ relayBaseUrl: 'http://h', daemonId: 'd', clientId: 'c' });
  const store = new s.RemoteStore({});
  const socket = memorySocket();
  const client = new s.RemoteRelayClient({ config: cfg, store, socketFactory: () => socket,
    setTimeout: () => 0, clearTimeout: () => {} });
  client.connect();
  socket.onmessage({ data: JSON.stringify(readyEnvelope('d')) });

  const ping = { v: 1, id: 'ping-1', daemon_id: 'd',
    source: { kind: 'daemon', id: 'd' },
    target: { kind: 'remote-client', id: 'user', user_id: 'user' },
    kind: 'ping', created_at: new Date().toISOString(), payload: {} };
  socket.onmessage({ data: JSON.stringify(ping) });
  socket.onmessage({ data: JSON.stringify(ping) }); // duplicate control

  assert.equal(socket.sent.filter((e) => e.kind === 'pong').length, 1,
    'control kind responds once and the duplicate is dropped');
  assert.equal(socket.sent.filter((e) => e.kind === 'ack').length, 0,
    'control kinds are never acked, even on duplicate');
});

test('malformed inbound frames are dropped without throwing', () => {
  const s = loadBundle();
  const cfg = s.RemoteConfig.parseConfig({ relayBaseUrl: 'http://h', daemonId: 'd', clientId: 'c' });
  const store = new s.RemoteStore({});
  const socket = memorySocket();
  const client = new s.RemoteRelayClient({ config: cfg, store, socketFactory: () => socket,
    setTimeout: () => 0, clearTimeout: () => {} });
  client.connect();
  assert.doesNotThrow(() => socket.onmessage({ data: 'not-json' }));
  assert.doesNotThrow(() => socket.onmessage({ data: JSON.stringify({ v: 9 }) }));
  assert.equal(store.agentList().length, 0);
});

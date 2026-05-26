// Remote web UI: relay wire-envelope build/validate/parse.
// Loads ee/frontend/remote/js/envelope.js into a vm sandbox (no DOM needed).

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function loadRemote() {
  const sandbox = { console };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  const src = fs.readFileSync(
    path.join(repoRoot, 'ee/frontend/remote/js/envelope.js'), 'utf8');
  vm.runInContext(src, sandbox, { filename: 'envelope.js' });
  return sandbox.RemoteEnvelope;
}

test('buildUserMessage produces a valid v1 client->daemon envelope', () => {
  const E = loadRemote();
  const env = E.buildUserMessage({
    daemonId: 'daemon-1', clientId: 'client-9', agentId: 'worker-7',
    message: 'hello there',
  });
  assert.equal(env.v, 1);
  assert.equal(env.kind, 'user_message');
  assert.equal(env.daemon_id, 'daemon-1');
  assert.equal(env.source.kind, 'remote-client');
  assert.equal(env.source.id, 'client-9');
  assert.equal(env.target.kind, 'daemon');
  assert.equal(env.target.id, 'daemon-1');
  assert.equal(env.payload.agent_id, 'worker-7');
  assert.equal(env.payload.message, 'hello there');
  assert.ok(env.id && env.id.length > 0);
  assert.ok(Number.isFinite(Date.parse(env.created_at)), 'created_at is ISO-8601');
});

test('ask answers ride on user_message via reply_to_id (NOT kind ask_reply)', () => {
  const E = loadRemote();
  const env = E.buildUserMessage({
    daemonId: 'd', clientId: 'c', agentId: 'a', message: 'my answer',
    replyToId: 'ask-123',
  });
  assert.equal(env.kind, 'user_message',
    'connector ingress only accepts user_message; ask answers must use it');
  assert.equal(env.payload.reply_to_id, 'ask-123');
});

test('buildAck carries ack_id + delivery_state', () => {
  const E = loadRemote();
  const ack = E.buildAck({ daemonId: 'd', clientId: 'c', ackId: 'msg-x', ackKind: 'agent_message' });
  assert.equal(ack.kind, 'ack');
  assert.equal(ack.payload.ack_id, 'msg-x');
  assert.equal(ack.payload.delivery_state, 'acked');
});

test('buildCommandRequest produces a valid privileged command envelope', () => {
  const E = loadRemote();
  const env = E.buildCommandRequest({
    daemonId: 'daemon-1', clientId: 'client-9', cmd: 'restart_agent',
    args: { agent_id: 'worker-7' }, confirm: false,
    commandId: '123e4567-e89b-42d3-a456-426614174000',
    issuedAt: '2026-05-26T12:00:00.000Z',
    nonce: 'nonce-1',
  });
  assert.equal(env.kind, 'command_request');
  assert.equal(env.payload.cmd, 'restart_agent');
  assert.equal(env.payload.args.agent_id, 'worker-7');
  assert.equal(env.payload.confirm, false);
  assert.equal(env.target.kind, 'daemon');
});

test('makeEnvelope rejects target daemon id mismatch (relay semantic rule)', () => {
  const E = loadRemote();
  assert.throws(() => E.makeEnvelope({
    daemon_id: 'd1',
    source: { kind: 'remote-client', id: 'c' },
    target: { kind: 'daemon', id: 'OTHER' },
    kind: 'user_message',
    payload: {},
  }), /target daemon id must match daemon_id/);
});

test('parseInbound accepts a valid agent_message and rejects malformed frames', () => {
  const E = loadRemote();
  const good = JSON.stringify({
    v: 1, id: 'msg-1', daemon_id: 'd',
    source: { kind: 'daemon', id: 'd' },
    target: { kind: 'remote-client', id: 'user', user_id: 'user' },
    kind: 'agent_message', created_at: new Date().toISOString(),
    payload: { agent_id: 'a', message: 'hi' },
  });
  const env = E.parseInbound(good);
  assert.equal(env.kind, 'agent_message');
  assert.equal(env.payload.agent_id, 'a');

  assert.throws(() => E.parseInbound('not json'), /./);
  assert.throws(() => E.parseInbound(JSON.stringify({ v: 2, id: 'x' })),
    /unsupported relay protocol version/);
  assert.throws(() => E.parseInbound(JSON.stringify({
    v: 1, id: 'x', source: { kind: 'daemon', id: 'd' },
    target: { kind: 'remote-client', id: 'u' }, kind: 'agent_message',
    created_at: new Date().toISOString(), payload: {},
  })), /daemon_id is required/);
});

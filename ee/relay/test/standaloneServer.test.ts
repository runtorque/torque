import assert from "node:assert/strict";
import test from "node:test";
import WebSocket from "ws";

import { makeAckEnvelope, makeRelayEnvelope, parseRelayEnvelopeJson, type RelayEnvelope } from "../src/core/protocol.js";
import { createStandaloneRelayServer, type StandaloneRelayServerHandle } from "../src/adapters/standalone/server.js";
import { LOCAL_DEV_AUTH_MODE } from "../src/core/auth.js";
import { createDaemonCredentialFixture, signedDaemonAttachHeader } from "./helpers/auth.js";
test("standalone Node entrypoint accepts HTTP enqueue, replays over daemon WS, and records ack", async () => {
  const relay = await createStandaloneRelayServer({ port: 0, databasePath: ":memory:", replayLimit: 10 });
  try {
    await relay.listen();
    const address = relay.server.address();
    if (!address || typeof address !== "object") {
      throw new Error(`expected server to bind to an address object, got ${String(address)}`);
    }
    const base = `http://127.0.0.1:${address.port}`;

    const envelope = makeRelayEnvelope({
      id: "msg-http-queued",
      daemon_id: "daemon-1",
      source: { kind: "remote-client", id: "browser-1", user_id: "user-1" },
      target: { kind: "daemon", id: "daemon-1" },
      kind: "user_message",
      created_at: "2026-05-22T00:00:00.000Z",
      payload: { agent_id: "agent-1", message: "from http" },
    });

    const enqueue = await fetch(`${base}/v1/messages/daemon-1`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(envelope),
    });
    assert.equal(enqueue.status, 202);
    const body = await enqueue.json() as { delivered: boolean; idempotent: boolean };
    assert.equal(body.delivered, false);
    assert.equal(body.idempotent, false);

    const daemon = await openRecordingWs(`ws://127.0.0.1:${address.port}/v1/daemon/daemon-1/ws`);
    const first = await nextEnvelope(daemon);
    const second = await nextEnvelope(daemon);
    const ready = [first, second].find((message) => message.kind === "ready");
    const replayed = [first, second].find((message) => message.id === "msg-http-queued");
    assert.ok(ready);
    assert.ok(replayed);
    assert.equal(ready.kind, "ready");
    assert.equal(ready.target.kind, "daemon");
    assert.equal(ready.target.id, "daemon-1");
    assert.equal(ready.payload.replayed, 1);
    assert.equal(replayed.id, "msg-http-queued");
    assert.equal(replayed.payload.message, "from http");

    const ack = makeAckEnvelope({
      id: "ack-http-queued",
      daemon_id: "daemon-1",
      source: { kind: "daemon", id: "daemon-1" },
      target: { kind: "relay", id: "standalone" },
      ack_id: "msg-http-queued",
      ack_kind: "user_message",
      delivery_state: "acked",
      created_at: "2026-05-22T00:00:01.000Z",
    });
    daemon.ws.send(JSON.stringify(ack));
    await waitFor(async () => (await relay.store.getMessage("msg-http-queued"))?.delivery_state === "acked");
    const saved = await relay.store.getMessage("msg-http-queued");
    assert.equal(saved?.acked_at, "2026-05-22T00:00:01.000Z");
    daemon.ws.close();
  } finally {
    await relay.close();
  }
});

type RecordingWs = {
  ws: WebSocket;
  messages: RelayEnvelope[];
  waiters: ((value: RelayEnvelope) => void)[];
  errors: Error[];
};

function openRecordingWs(url: string, headers: Record<string, string> = {}): Promise<RecordingWs> {
  const recording: RecordingWs = {
    ws: new WebSocket(url, { headers }),
    messages: [],
    waiters: [],
    errors: [],
  };
  recording.ws.on("message", (data) => {
    const envelope = parseRelayEnvelopeJson(data.toString());
    const waiter = recording.waiters.shift();
    if (waiter) waiter(envelope);
    else recording.messages.push(envelope);
  });
  recording.ws.on("error", (error) => {
    recording.errors.push(error);
  });
  return new Promise((resolve, reject) => {
    recording.ws.once("open", () => resolve(recording));
    recording.ws.once("error", reject);
  });
}

function nextEnvelope(recording: RecordingWs): Promise<RelayEnvelope> {
  const existing = recording.messages.shift();
  if (existing) return Promise.resolve(existing);
  if (recording.errors[0]) return Promise.reject(recording.errors[0]);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("timed out waiting for relay envelope")), 2_000);
    recording.waiters.push((envelope) => {
      clearTimeout(timer);
      resolve(envelope);
    });
  });
}

async function waitFor(predicate: () => Promise<boolean>, timeoutMs = 2_000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("timed out waiting for condition");
}

test("standalone authenticated owner attach rejects hijack matrix without owner replacement or epoch increment", async () => {
  const relay = await createStandaloneRelayServer({ port: 0, databasePath: ":memory:", replayLimit: 10, authMode: "required" });
  try {
    await relay.listen();
    const address = relay.server.address();
    if (!address || typeof address !== "object") throw new Error("expected bound address");
    const wsUrl = `ws://127.0.0.1:${address.port}/v1/daemon/daemon-1/ws`;
    const fixture = await createDaemonCredentialFixture(relay.store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
    const daemon = await openRecordingWs(wsUrl, { authorization: await signedDaemonAttachHeader(fixture) });
    const ready = await nextEnvelope(daemon);
    assert.equal(ready.kind, "ready");
    const initial = await relay.coordinator.snapshot("daemon-1");
    assert.equal(initial.epoch, 1);
    assert.equal(initial.daemon_online, true);

    await assertWsRejected(wsUrl, {}, 401);
    await assertNoOwnerChange(relay, "daemon-1", initial.daemon_connection_id, 1);
    assert.equal(daemon.ws.readyState, WebSocket.OPEN);

    const wrongSignature = (await signedDaemonAttachHeader(fixture)).replace(/signature="[^"]+"/, 'signature="bogus"');
    await assertWsRejected(wsUrl, { authorization: wrongSignature }, 401);
    await assertNoOwnerChange(relay, "daemon-1", initial.daemon_connection_id, 1);

    const replayHeader = await signedDaemonAttachHeader(fixture, { nonce: "replay-once" });
    const replacement = await openRecordingWs(wsUrl, { authorization: replayHeader });
    await nextEnvelope(replacement);
    const afterReplacement = await relay.coordinator.snapshot("daemon-1");
    assert.equal(afterReplacement.epoch, 2);
    await assertWsRejected(wsUrl, { authorization: replayHeader }, 401);
    await assertNoOwnerChange(relay, "daemon-1", afterReplacement.daemon_connection_id, 2);

    const attacker = await createDaemonCredentialFixture(relay.store, { daemonId: "daemon-1", ownerUserId: "owner-2", credentialId: "cred-owner-2" });
    await assertWsRejected(wsUrl, { authorization: await signedDaemonAttachHeader(attacker) }, 403);
    await assertNoOwnerChange(relay, "daemon-1", afterReplacement.daemon_connection_id, 2);
    assert.equal(replacement.ws.readyState, WebSocket.OPEN);
    replacement.ws.close();
    daemon.ws.close();
  } finally {
    await relay.close();
  }
});

test("standalone non-loopback bind structurally forbids unauthenticated mode and rejects unauth attach before mutation", async () => {
  await assert.rejects(
    () => createStandaloneRelayServer({ port: 0, host: "0.0.0.0", databasePath: ":memory:", authMode: LOCAL_DEV_AUTH_MODE }),
    /non-loopback/,
  );
  const relay = await createStandaloneRelayServer({ port: 0, host: "0.0.0.0", databasePath: ":memory:" });
  try {
    await relay.listen();
    const address = relay.server.address();
    if (!address || typeof address !== "object") throw new Error("expected bound address");
    await assertWsRejected(`ws://127.0.0.1:${address.port}/v1/daemon/daemon-1/ws`, {}, 401);
    const snapshot = await relay.coordinator.snapshot("daemon-1");
    assert.equal(snapshot.epoch, 0);
    assert.equal(snapshot.daemon_online, false);
    assert.equal(await relay.store.getInstance("daemon-1"), null);
  } finally {
    await relay.close();
  }
});

async function assertNoOwnerChange(relay: StandaloneRelayServerHandle, daemonId: string, connectionId: string, epoch: number): Promise<void> {
  const snapshot = await relay.coordinator.snapshot(daemonId);
  assert.equal(snapshot.daemon_connection_id, connectionId);
  assert.equal(snapshot.epoch, epoch);
  assert.equal(snapshot.daemon_online, true);
}

function assertWsRejected(url: string, headers: Record<string, string>, status: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url, { headers });
    const timer = setTimeout(() => reject(new Error("timed out waiting for websocket rejection")), 2_000);
    ws.once("open", () => {
      clearTimeout(timer);
      ws.close();
      reject(new Error("websocket unexpectedly opened"));
    });
    ws.once("unexpected-response", (_req, res) => {
      clearTimeout(timer);
      assert.equal(res.statusCode, status);
      resolve();
    });
    ws.once("error", (error) => {
      if ((error as Error).message.includes("Unexpected server response")) return;
      clearTimeout(timer);
      reject(error);
    });
  });
}

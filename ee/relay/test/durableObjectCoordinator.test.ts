import assert from "node:assert/strict";
import test from "node:test";

import { makeRelayEnvelope, parseRelayEnvelopeJson, type RelayEnvelope } from "../src/core/protocol.js";
import {
  DaemonRendezvousDurableObject,
  type DurableObjectSessionAttachment,
} from "../src/adapters/cloudflare/durableObjectCoordinator.js";
import { SqliteRelayStore } from "../src/adapters/standalone/sqliteStore.js";
import { hashSecret } from "../src/core/auth.js";
import { createClientSessionFixture, createDaemonCredentialFixture, signedDaemonAttachHeader } from "./helpers/auth.js";

class FakeDurableObjectState {
  constructor(private readonly sockets: FakeCfWebSocket[]) {}
  getWebSockets(): WebSocket[] {
    return this.sockets as unknown as WebSocket[];
  }
  acceptWebSocket(ws: WebSocket): void {
    this.sockets.push(ws as unknown as FakeCfWebSocket);
  }
}

class FakeCfWebSocket {
  readonly sent: string[] = [];
  readonly closes: { code?: number; reason?: string }[] = [];
  constructor(private attachment: DurableObjectSessionAttachment) {}
  send(text: string): void {
    this.sent.push(String(text));
  }
  close(code?: number, reason?: string): void {
    this.closes.push({ code, reason });
  }
  serializeAttachment(value: DurableObjectSessionAttachment): void {
    this.attachment = { ...value };
  }
  deserializeAttachment(): DurableObjectSessionAttachment {
    return { ...this.attachment };
  }
  envelopes(): RelayEnvelope[] {
    return this.sent.map((text) => parseRelayEnvelopeJson(text));
  }
}

test("Durable Object rehydrates hibernated sockets and fences stale daemon epochs", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
  await createClientSessionFixture(store, { ownerUserId: "owner-1", sessionId: "session-1", token: "client-token" });
  const oldDaemon = new FakeCfWebSocket({
    role: "daemon",
    daemonId: "daemon-1",
    clientId: "",
    connectionId: "daemon-old",
    epoch: 1,
    ownerUserId: "owner-1",
    credentialId: "cred-1",
  });
  const newDaemon = new FakeCfWebSocket({
    role: "daemon",
    daemonId: "daemon-1",
    clientId: "",
    connectionId: "daemon-new",
    epoch: 2,
    ownerUserId: "owner-1",
    credentialId: "cred-1",
  });
  const client = new FakeCfWebSocket({
    role: "client",
    daemonId: "daemon-1",
    clientId: "client-1",
    connectionId: "client-1",
    epoch: 0,
    ownerUserId: "owner-1",
    sessionId: "session-1",
    userId: "user",
  });
  const state = new FakeDurableObjectState([oldDaemon, newDaemon, client]);
  const durableObject = new DaemonRendezvousDurableObject(state as unknown as DurableObjectState, { authStore: store });

  await durableObject.rehydrateHibernatedSocketsForTest();

  assert.equal(oldDaemon.closes.some((close) => close.code === 4000 && close.reason === "replaced_by_new_daemon_connection"), true);
  assert.equal(newDaemon.envelopes().find((envelope) => envelope.kind === "ready")?.target.kind, "daemon");
  assert.equal(client.envelopes().find((envelope) => envelope.kind === "ready")?.target.kind, "remote-client");
  const snapshot = await durableObject.snapshotForTest("daemon-1");
  assert.equal(snapshot.daemon_online, true);
  assert.equal(snapshot.daemon_connection_id, "daemon-new");
  assert.equal(snapshot.epoch, 2);
  assert.deepEqual(snapshot.client_connection_ids, ["client-1"]);

  const clientMessage = makeRelayEnvelope({
    id: "msg-client",
    daemon_id: "daemon-1",
    source: { kind: "remote-client", id: "client-1" },
    target: { kind: "daemon", id: "daemon-1" },
    kind: "user_message",
    created_at: "2026-05-22T00:00:00.000Z",
    payload: { message: "from client" },
  });
  await durableObject.webSocketMessage(client as unknown as WebSocket, JSON.stringify(clientMessage));
  const forwardedClientMessage = newDaemon.envelopes().find((envelope) => envelope.id === "msg-client");
  assert.ok(forwardedClientMessage);
  assert.equal(forwardedClientMessage.source.user_id, "user");
  assert.equal(oldDaemon.envelopes().some((envelope) => envelope.id === "msg-client"), false);

  const staleDaemonMessage = makeRelayEnvelope({
    id: "msg-stale",
    daemon_id: "daemon-1",
    source: { kind: "daemon", id: "daemon-1" },
    target: { kind: "remote-client", id: "client-1" },
    kind: "agent_message",
    created_at: "2026-05-22T00:00:01.000Z",
    payload: { message: "from stale daemon" },
  });
  await durableObject.webSocketMessage(oldDaemon as unknown as WebSocket, JSON.stringify(staleDaemonMessage));
  assert.equal(oldDaemon.closes.some((close) => close.code === 4001 && close.reason === "stale_daemon_connection"), true);
  assert.equal(client.envelopes().some((envelope) => envelope.id === "msg-stale"), false);

  const currentDaemonMessage = makeRelayEnvelope({
    id: "msg-current",
    daemon_id: "daemon-1",
    source: { kind: "daemon", id: "daemon-1" },
    target: { kind: "remote-client", id: "client-1" },
    kind: "agent_message",
    created_at: "2026-05-22T00:00:02.000Z",
    payload: { message: "from current daemon" },
  });
  await durableObject.webSocketMessage(newDaemon as unknown as WebSocket, JSON.stringify(currentDaemonMessage));
  assert.equal(client.envelopes().some((envelope) => envelope.id === "msg-current"), true);
});

test("Durable Object rehydrate preserves latest daemon owner when Cloudflare returns sockets in reverse epoch order", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
  const currentDaemon = new FakeCfWebSocket({
    role: "daemon",
    daemonId: "daemon-1",
    clientId: "",
    connectionId: "daemon-current",
    epoch: 2,
    ownerUserId: "owner-1",
    credentialId: "cred-1",
  });
  const staleDaemon = new FakeCfWebSocket({
    role: "daemon",
    daemonId: "daemon-1",
    clientId: "",
    connectionId: "daemon-stale",
    epoch: 1,
    ownerUserId: "owner-1",
    credentialId: "cred-1",
  });
  const state = new FakeDurableObjectState([currentDaemon, staleDaemon]);
  const durableObject = new DaemonRendezvousDurableObject(state as unknown as DurableObjectState, { authStore: store });

  await durableObject.rehydrateHibernatedSocketsForTest();

  const snapshot = await durableObject.snapshotForTest("daemon-1");
  assert.equal(snapshot.daemon_connection_id, "daemon-current");
  assert.equal(snapshot.epoch, 2);
  assert.equal(staleDaemon.closes.some((close) => close.code === 4000), true);
  assert.equal(currentDaemon.closes.some((close) => close.code === 4000), false);
});


test("Durable Object closes hibernated daemon when current instance owner changed", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
  await store.upsertInstance({
    id: "daemon-1",
    owner_user_id: "owner-2",
    label: "daemon-1",
    created_at: "2026-05-23T00:01:00.000Z",
    last_seen_at: "2026-05-23T00:01:00.000Z",
    fencing_epoch: 0,
    active_credential_id: "",
    coordination_updated_at: "2026-05-23T00:01:00.000Z",
    metadata: {},
  });
  const staleDaemon = new FakeCfWebSocket({
    role: "daemon",
    daemonId: "daemon-1",
    clientId: "",
    connectionId: "owner1-old",
    epoch: 1,
    ownerUserId: "owner-1",
    credentialId: "cred-1",
  });
  const durableObject = new DaemonRendezvousDurableObject(
    new FakeDurableObjectState([staleDaemon]) as unknown as DurableObjectState,
    { authStore: store },
  );

  await durableObject.rehydrateHibernatedSocketsForTest();

  assert.equal(staleDaemon.closes.some((close) => close.code === 4003 && close.reason === "authenticated_session_revoked"), true);
  const snapshot = await durableObject.snapshotForTest("daemon-1");
  assert.equal(snapshot.daemon_online, false);
  assert.equal(snapshot.daemon_connection_id, "");
  assert.equal(snapshot.epoch, 0);
  assert.equal((await store.getInstance("daemon-1"))?.owner_user_id, "owner-2");
});

test("Durable Object closes hibernated client when current instance owner changed", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
  await createClientSessionFixture(store, { ownerUserId: "owner-1", sessionId: "session-1", token: "client-token" });
  await store.upsertInstance({
    id: "daemon-1",
    owner_user_id: "owner-2",
    label: "daemon-1",
    created_at: "2026-05-23T00:01:00.000Z",
    last_seen_at: "2026-05-23T00:01:00.000Z",
    fencing_epoch: 0,
    active_credential_id: "",
    coordination_updated_at: "2026-05-23T00:01:00.000Z",
    metadata: {},
  });
  const staleClient = new FakeCfWebSocket({
    role: "client",
    daemonId: "daemon-1",
    clientId: "client-1",
    connectionId: "owner1-client",
    epoch: 0,
    ownerUserId: "owner-1",
    sessionId: "session-1",
    userId: "user",
  });
  const durableObject = new DaemonRendezvousDurableObject(
    new FakeDurableObjectState([staleClient]) as unknown as DurableObjectState,
    { authStore: store },
  );

  await durableObject.rehydrateHibernatedSocketsForTest();

  assert.equal(staleClient.closes.some((close) => close.code === 4003 && close.reason === "authenticated_session_revoked"), true);
  const snapshot = await durableObject.snapshotForTest("daemon-1");
  assert.deepEqual(snapshot.client_connection_ids, []);
  assert.equal(snapshot.daemon_online, false);
  assert.equal((await store.getInstance("daemon-1"))?.owner_user_id, "owner-2");
});


test("Durable Object rejects daemon attach hijack matrix without owner replacement or epoch increment", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const ownerOne = await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-owner-1" });
  await store.createDaemonCredential({
    credential_id: "cred-owner-2",
    daemon_id: "daemon-1",
    owner_user_id: "owner-2",
    public_key_jwk: ownerOne.publicKeyJwk as any,
    alg: "ES256",
    created_at: "2026-05-23T00:00:00.000Z",
    last_used_at: "",
    revoked_at: "",
    metadata: {},
  });
  const currentDaemon = new FakeCfWebSocket({
    role: "daemon",
    daemonId: "daemon-1",
    clientId: "",
    connectionId: "daemon-current",
    epoch: 1,
    ownerUserId: "owner-1",
    credentialId: "cred-owner-1",
  });
  const state = new FakeDurableObjectState([currentDaemon]);
  const durableObject = new DaemonRendezvousDurableObject(state as unknown as DurableObjectState, { authStore: store });
  await durableObject.rehydrateHibernatedSocketsForTest();
  const before = await durableObject.snapshotForTest("daemon-1");
  assert.equal(before.daemon_connection_id, "daemon-current");
  assert.equal(before.epoch, 1);

  await assert.rejects(
    () => attachDaemonForTest(durableObject, {}, "missing-auth"),
    /Authorization header is required/,
  );
  await assertNoDurableOwnerChange(durableObject, "daemon-current", 1);

  const wrongSignature = (await signedDaemonAttachHeader(ownerOne)).replace(/signature="[^"]+"/, 'signature="bogus"');
  await assert.rejects(
    () => attachDaemonForTest(durableObject, { authorization: wrongSignature }, "wrong-signature"),
    /signature is invalid/,
  );
  await assertNoDurableOwnerChange(durableObject, "daemon-current", 1);

  const replayHeader = await signedDaemonAttachHeader(ownerOne, { nonce: "do-replay-once" });
  const replacement = new FakeCfWebSocket({ role: "daemon", daemonId: "daemon-1", clientId: "", connectionId: "replacement", epoch: 0 });
  await durableObject.attachSocketForTest(
    replacement as unknown as WebSocket,
    { role: "daemon", daemonId: "daemon-1", clientId: "" },
    daemonAttachRequest({ authorization: replayHeader }),
  );
  const afterReplacement = await durableObject.snapshotForTest("daemon-1");
  assert.equal(afterReplacement.daemon_connection_id, "daemon:daemon-1:daemon:test");
  assert.equal(afterReplacement.epoch, 2);
  await assert.rejects(
    () => attachDaemonForTest(durableObject, { authorization: replayHeader }, "replayed"),
    /nonce has already been used/,
  );
  await assertNoDurableOwnerChange(durableObject, "daemon:daemon-1:daemon:test", 2);

  const wrongOwnerHeader = await signedDaemonAttachHeader({ ...ownerOne, credentialId: "cred-owner-2", ownerUserId: "owner-2" });
  await assert.rejects(
    () => attachDaemonForTest(durableObject, { authorization: wrongOwnerHeader }, "wrong-owner"),
    /owner does not match/,
  );
  await assertNoDurableOwnerChange(durableObject, "daemon:daemon-1:daemon:test", 2);
  assert.equal((await store.getInstance("daemon-1"))?.owner_user_id, "owner-1");
  assert.equal(currentDaemon.closes.some((close) => close.code === 4000), true);
  assert.equal(replacement.closes.some((close) => close.code === 4000), false);
});


test("Durable Object rejects wrong-owner authenticated daemon attach without replacing current owner", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const ownerOne = await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-owner-1" });
  await store.createDaemonCredential({
    credential_id: "cred-owner-2",
    daemon_id: "daemon-1",
    owner_user_id: "owner-2",
    public_key_jwk: ownerOne.publicKeyJwk as any,
    alg: "ES256",
    created_at: "2026-05-23T00:00:00.000Z",
    last_used_at: "",
    revoked_at: "",
    metadata: {},
  });
  const currentDaemon = new FakeCfWebSocket({
    role: "daemon",
    daemonId: "daemon-1",
    clientId: "",
    connectionId: "daemon-current",
    epoch: 1,
    ownerUserId: "owner-1",
    credentialId: "cred-owner-1",
  });
  const state = new FakeDurableObjectState([currentDaemon]);
  const durableObject = new DaemonRendezvousDurableObject(state as unknown as DurableObjectState, { authStore: store });
  await durableObject.rehydrateHibernatedSocketsForTest();
  const before = await durableObject.snapshotForTest("daemon-1");
  assert.equal(before.daemon_connection_id, "daemon-current");
  assert.equal(before.epoch, 1);

  const attacker = new FakeCfWebSocket({ role: "daemon", daemonId: "daemon-1", clientId: "", connectionId: "attacker", epoch: 0 });
  const wrongOwnerHeader = await signedDaemonAttachHeader({ ...ownerOne, credentialId: "cred-owner-2", ownerUserId: "owner-2" });
  await assert.rejects(
    () => durableObject.attachSocketForTest(
      attacker as unknown as WebSocket,
      { role: "daemon", daemonId: "daemon-1", clientId: "" },
      new Request("https://relay.example.com/v1/daemon/daemon-1/ws", {
        method: "GET",
        headers: { authorization: wrongOwnerHeader },
      }),
    ),
    /owner does not match/,
  );
  const after = await durableObject.snapshotForTest("daemon-1");
  assert.equal(after.daemon_connection_id, "daemon-current");
  assert.equal(after.epoch, 1);
  assert.equal(currentDaemon.closes.some((close) => close.code === 4000), false);
});

test("Durable Object mints an establish code on the authed daemon WS and rejects replays + stale epochs", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const fixture = await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
  const durableObject = new DaemonRendezvousDurableObject(
    new FakeDurableObjectState([]) as unknown as DurableObjectState,
    { authStore: store },
  );

  const daemonWs = new FakeCfWebSocket({ role: "daemon", daemonId: "daemon-1", clientId: "", connectionId: "mint-daemon", epoch: 0 });
  await durableObject.attachSocketForTest(
    daemonWs as unknown as WebSocket,
    { role: "daemon", daemonId: "daemon-1", clientId: "" },
    daemonAttachRequest({ authorization: await signedDaemonAttachHeader(fixture, { nonce: "mint-attach-1" }) }),
  );

  const mintEnvelope = (id: string, nonce: string): RelayEnvelope => makeRelayEnvelope({
    id,
    daemon_id: "daemon-1",
    source: { kind: "daemon", id: "daemon-1" },
    target: { kind: "relay", id: "relay" },
    kind: "mint_client_establish_code",
    payload: { nonce, label: "iphone" },
  });

  await durableObject.webSocketMessage(daemonWs as unknown as WebSocket, JSON.stringify(mintEnvelope("mint-req-1", "do-mint-nonce-1")));
  const result = daemonWs.envelopes().find((env) => env.kind === "mint_client_establish_code_result" && env.payload.ref_id === "mint-req-1");
  assert.ok(result, "expected a mint result envelope");
  const code = String(result!.payload.code || "");
  assert.ok(code.length >= 32);
  assert.equal(result!.payload.owner_user_id, "owner-1");
  assert.equal(result!.target.kind, "daemon");

  // Hash-only persistence: redeem by hash works, by raw fails (raw never stored).
  assert.equal(await store.consumeClientEstablishCode(code), null);
  const redeemed = await store.consumeClientEstablishCode(await hashSecret(code));
  assert.equal(redeemed?.owner_user_id, "owner-1");

  // Replay: a reused nonce yields a typed error, not a second code.
  await durableObject.webSocketMessage(daemonWs as unknown as WebSocket, JSON.stringify(mintEnvelope("mint-req-2", "do-mint-nonce-1")));
  const replayError = daemonWs.envelopes().find((env) => env.kind === "error" && env.payload.ref_id === "mint-req-2");
  assert.ok(replayError);
  assert.equal(replayError!.payload.code, "replayed_mint_nonce");

  // Stale epoch: a newer owner connection supersedes this socket; minting on the
  // now-stale socket is fenced (P5 connection-level fencing).
  const replacement = new FakeCfWebSocket({ role: "daemon", daemonId: "daemon-1", clientId: "", connectionId: "mint-daemon-2", epoch: 0 });
  await durableObject.attachSocketForTest(
    replacement as unknown as WebSocket,
    { role: "daemon", daemonId: "daemon-1", clientId: "" },
    daemonAttachRequest({ authorization: await signedDaemonAttachHeader(fixture, { nonce: "mint-attach-2" }) }),
  );
  await durableObject.webSocketMessage(daemonWs as unknown as WebSocket, JSON.stringify(mintEnvelope("mint-req-3", "do-mint-nonce-2")));
  const staleError = daemonWs.envelopes().find((env) => env.kind === "error" && env.payload.ref_id === "mint-req-3");
  assert.ok(staleError);
  assert.equal(staleError!.payload.code, "stale_daemon_connection");
  await store.close();
});

function daemonAttachRequest(headers: Record<string, string>): Request {
  return new Request("https://relay.example.com/v1/daemon/daemon-1/ws", {
    method: "GET",
    headers,
  });
}

async function attachDaemonForTest(
  durableObject: DaemonRendezvousDurableObject,
  headers: Record<string, string>,
  connectionLabel: string,
): Promise<DurableObjectSessionAttachment> {
  const ws = new FakeCfWebSocket({
    role: "daemon",
    daemonId: "daemon-1",
    clientId: "",
    connectionId: connectionLabel,
    epoch: 0,
  });
  return durableObject.attachSocketForTest(
    ws as unknown as WebSocket,
    { role: "daemon", daemonId: "daemon-1", clientId: "" },
    daemonAttachRequest(headers),
  );
}

async function assertNoDurableOwnerChange(
  durableObject: DaemonRendezvousDurableObject,
  connectionId: string,
  epoch: number,
): Promise<void> {
  const snapshot = await durableObject.snapshotForTest("daemon-1");
  assert.equal(snapshot.daemon_connection_id, connectionId);
  assert.equal(snapshot.epoch, epoch);
  assert.equal(snapshot.daemon_online, true);
}

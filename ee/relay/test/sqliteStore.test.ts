import assert from "node:assert/strict";
import test from "node:test";

import { makeRelayEnvelope } from "../src/core/protocol.js";
import { RelayConflictError } from "../src/core/errors.js";
import { SqliteRelayStore } from "../src/adapters/standalone/sqliteStore.js";

function envelope(id = "msg-1", message = "hello") {
  return makeRelayEnvelope({
    id,
    daemon_id: "daemon-1",
    source: { kind: "remote-client", id: "browser-1", user_id: "user-1" },
    target: { kind: "daemon", id: "daemon-1" },
    kind: "user_message",
    created_at: "2026-05-22T00:00:02.000Z",
    payload: { agent_id: "agent-1", message },
  });
}

test("SqliteRelayStore migrates and persists instances/messages through the RelayStore port", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const instance = await store.upsertInstance({
    id: "daemon-1",
    owner_user_id: "user-1",
    label: "Laptop",
    created_at: "2026-05-22T00:00:00.000Z",
    last_seen_at: "2026-05-22T00:00:01.000Z",
    fencing_epoch: 3,
    active_credential_id: "cred-1",
    coordination_updated_at: "2026-05-22T00:00:01.000Z",
    metadata: { profile: "desktop" },
  });

  assert.equal(instance.id, "daemon-1");
  assert.equal(instance.fencing_epoch, 3);
  assert.equal(instance.active_credential_id, "cred-1");
  assert.deepEqual((await store.getInstance("daemon-1"))?.metadata, { profile: "desktop" });

  const saved = await store.appendMessage(envelope(), "to_daemon");
  assert.equal(saved.id, "msg-1");
  assert.equal(saved.direction, "to_daemon");
  assert.equal(saved.delivery_state, "pending");
  assert.deepEqual(saved.payload, { agent_id: "agent-1", message: "hello" });

  const duplicate = await store.appendMessageResult(envelope(), "to_daemon");
  assert.equal(duplicate.inserted, false);
  assert.equal(duplicate.idempotent, true);

  await assert.rejects(
    () => store.appendMessage(envelope("msg-1", "different"), "to_daemon"),
    RelayConflictError,
  );

  assert.equal((await store.listPendingMessages("daemon-1", { direction: "to_daemon" })).length, 1);
  const attempted = await store.markDeliveryAttempt("msg-1", 7, "2026-05-22T00:00:03.000Z");
  assert.equal(attempted?.delivery_state, "delivered");
  assert.equal(attempted?.delivery_attempts, 1);
  assert.equal(attempted?.last_delivery_epoch, 7);
  assert.equal((await store.listPendingMessages("daemon-1", { direction: "to_daemon" })).length, 1);
  const acked = await store.markAcked("msg-1", "2026-05-22T00:00:04.000Z");
  assert.equal(acked?.delivery_state, "acked");
  assert.equal(acked?.acked_at, "2026-05-22T00:00:04.000Z");
  assert.equal((await store.listPendingMessages("daemon-1", { direction: "to_daemon" })).length, 0);
  await store.close();
});

test("SqliteRelayStore persists auth credentials, sessions, and nonce replay guard", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const pairing = await store.createPairingToken({
    id: "pair-1",
    token_hash: "hash-pair-1",
    owner_user_id: "owner-1",
    daemon_id: "daemon-1",
    label: "Laptop",
    created_at: "2026-05-23T00:00:00.000Z",
    expires_at: "2099-01-01T00:00:00.000Z",
    consumed_at: "",
    revoked_at: "",
    metadata: { ok: true },
  });
  assert.equal(pairing.owner_user_id, "owner-1");
  assert.equal((await store.consumePairingToken("hash-pair-1", "2026-05-23T00:01:00.000Z"))?.consumed_at, "2026-05-23T00:01:00.000Z");
  assert.equal(await store.consumePairingToken("hash-pair-1", "2026-05-23T00:02:00.000Z"), null);

  await store.createDaemonCredential({
    credential_id: "cred-1",
    daemon_id: "daemon-1",
    owner_user_id: "owner-1",
    public_key_jwk: { kty: "EC", crv: "P-256", x: "x", y: "y" },
    alg: "ES256",
    created_at: "2026-05-23T00:00:00.000Z",
    last_used_at: "",
    revoked_at: "",
    metadata: {},
  });
  assert.equal((await store.getDaemonCredential("daemon-1", "cred-1"))?.owner_user_id, "owner-1");
  assert.equal(await store.recordAuthNonce("cred-1", "nonce-hash", "2099-01-01T00:00:00.000Z"), true);
  assert.equal(await store.recordAuthNonce("cred-1", "nonce-hash", "2099-01-01T00:00:00.000Z"), false);
  assert.equal((await store.revokeDaemonCredential("cred-1", "2026-05-23T00:03:00.000Z"))?.revoked_at, "2026-05-23T00:03:00.000Z");

  await store.createClientSession({
    session_id: "session-1",
    token_hash: "hash-session-1",
    owner_user_id: "owner-1",
    created_at: "2026-05-23T00:00:00.000Z",
    expires_at: "2099-01-01T00:00:00.000Z",
    revoked_at: "",
    metadata: {},
  });
  assert.equal((await store.getClientSessionByTokenHash("hash-session-1"))?.session_id, "session-1");
  assert.equal((await store.getClientSession("session-1"))?.owner_user_id, "owner-1");
  assert.equal((await store.revokeClientSession("session-1", "2026-05-23T00:04:00.000Z"))?.revoked_at, "2026-05-23T00:04:00.000Z");
  await store.close();
});

test("SqliteRelayStore claimInstanceOwner enforces owner-CAS and monotonic fencing epochs", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const first = await store.claimInstanceOwner({
    id: "daemon-cas",
    ownerUserId: "owner-1",
    credentialId: "cred-1",
    fencingEpoch: 1,
    now: "2026-05-23T00:00:00.000Z",
  });
  assert.equal(first.claimed, true);
  assert.equal(first.record?.fencing_epoch, 1);

  const sameOwnerHigherEpoch = await store.claimInstanceOwner({
    id: "daemon-cas",
    ownerUserId: "owner-1",
    credentialId: "cred-1",
    fencingEpoch: 2,
    now: "2026-05-23T00:00:01.000Z",
  });
  assert.equal(sameOwnerHigherEpoch.claimed, true);
  assert.equal(sameOwnerHigherEpoch.record?.fencing_epoch, 2);

  const stale = await store.claimInstanceOwner({
    id: "daemon-cas",
    ownerUserId: "owner-1",
    credentialId: "cred-1",
    fencingEpoch: 1,
  });
  assert.equal(stale.claimed, false);
  assert.equal(stale.reason, "stale_fencing_epoch");

  const wrongOwner = await store.claimInstanceOwner({
    id: "daemon-cas",
    ownerUserId: "owner-2",
    credentialId: "cred-2",
    fencingEpoch: 3,
  });
  assert.equal(wrongOwner.claimed, false);
  assert.equal(wrongOwner.reason, "daemon_owner_mismatch");
  assert.equal((await store.getInstance("daemon-cas"))?.owner_user_id, "owner-1");
  assert.equal((await store.getInstance("daemon-cas"))?.fencing_epoch, 2);
  await store.close();
});

test("SqliteRelayStore upsertInstance does not lower persisted fencing_epoch", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  await store.claimInstanceOwner({
    id: "daemon-rotation",
    ownerUserId: "owner-1",
    credentialId: "cred-1",
    fencingEpoch: 7,
    now: "2026-05-23T00:00:00.000Z",
  });

  const rotated = await store.upsertInstance({
    id: "daemon-rotation",
    owner_user_id: "owner-1",
    label: "rotated",
    created_at: "2026-05-23T00:00:00.000Z",
    last_seen_at: "2026-05-23T00:01:00.000Z",
    fencing_epoch: 0,
    active_credential_id: "cred-2",
    coordination_updated_at: "2026-05-23T00:01:00.000Z",
    metadata: {},
  });

  assert.equal(rotated.active_credential_id, "cred-2");
  assert.equal(rotated.fencing_epoch, 7);
  assert.equal((await store.getInstance("daemon-rotation"))?.fencing_epoch, 7);
  await store.close();
});

test("SqliteRelayStore client-establish codes are single-use, hashed, and expiry-gated", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  await store.createClientEstablishCode({
    id: "establish-1",
    code_hash: "hash-code-1",
    owner_user_id: "owner-1",
    daemon_id: "daemon-1",
    label: "QR",
    created_at: "2026-05-23T00:00:00.000Z",
    expires_at: "2099-01-01T00:00:00.000Z",
    consumed_at: "",
    revoked_at: "",
    metadata: {},
  });
  // First redemption wins and stamps consumed_at.
  const first = await store.consumeClientEstablishCode("hash-code-1", "2026-05-23T00:01:00.000Z");
  assert.equal(first?.owner_user_id, "owner-1");
  assert.equal(first?.consumed_at, "2026-05-23T00:01:00.000Z");
  // Second redemption returns null (already consumed) — no double-mint.
  assert.equal(await store.consumeClientEstablishCode("hash-code-1", "2026-05-23T00:02:00.000Z"), null);
  // An expired code can never be redeemed.
  await store.createClientEstablishCode({
    id: "establish-2",
    code_hash: "hash-code-2",
    owner_user_id: "owner-1",
    daemon_id: "",
    label: "",
    created_at: "2026-05-23T00:00:00.000Z",
    expires_at: "2000-01-01T00:00:00.000Z",
    consumed_at: "",
    revoked_at: "",
    metadata: {},
  });
  assert.equal(await store.consumeClientEstablishCode("hash-code-2", "2026-05-23T00:03:00.000Z"), null);
  await store.close();
});

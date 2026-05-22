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
    metadata: { profile: "desktop" },
  });

  assert.equal(instance.id, "daemon-1");
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

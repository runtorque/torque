import assert from "node:assert/strict";
import test from "node:test";

import { makeRelayEnvelope } from "../src/core/protocol.js";
import { SqliteRelayStore } from "../src/adapters/standalone/sqliteStore.js";

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

  const envelope = makeRelayEnvelope({
    id: "msg-1",
    daemon_id: "daemon-1",
    source: { kind: "remote-client", id: "browser-1", user_id: "user-1" },
    target: { kind: "daemon", id: "daemon-1" },
    kind: "user_message",
    created_at: "2026-05-22T00:00:02.000Z",
    payload: { agent_id: "agent-1", message: "hello" },
  });

  const saved = await store.appendMessage(envelope, "to_daemon");
  assert.equal(saved.id, "msg-1");
  assert.equal(saved.direction, "to_daemon");
  assert.deepEqual(saved.payload, { agent_id: "agent-1", message: "hello" });

  const listed = await store.listMessages("daemon-1", { direction: "to_daemon" });
  assert.equal(listed.length, 1);
  assert.equal(listed[0].id, "msg-1");

  const delivered = await store.markDelivered("msg-1", "2026-05-22T00:00:03.000Z");
  assert.equal(delivered?.delivered_at, "2026-05-22T00:00:03.000Z");
  const acked = await store.markAcked("msg-1", "2026-05-22T00:00:04.000Z");
  assert.equal(acked?.acked_at, "2026-05-22T00:00:04.000Z");
  await store.close();
});

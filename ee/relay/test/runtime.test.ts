import assert from "node:assert/strict";
import test from "node:test";

import { RelayRuntime } from "../src/core/runtime.js";
import { makeAckEnvelope, makeRelayEnvelope } from "../src/core/protocol.js";
import { StandaloneRegistryCoordinator } from "../src/adapters/standalone/registryCoordinator.js";
import { SqliteRelayStore } from "../src/adapters/standalone/sqliteStore.js";
import { MemorySocket } from "./helpers/memorySocket.js";

test("RelayRuntime queues remote messages while daemon offline, replays on attach, and acks idempotently", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const coordinator = new StandaloneRegistryCoordinator();
  const runtime = new RelayRuntime(store, coordinator, { replayLimit: 5, relayId: "test-relay" });

  const queued = makeRelayEnvelope({
    id: "msg-offline",
    daemon_id: "daemon-1",
    source: { kind: "remote-client", id: "browser-1", user_id: "user-1" },
    target: { kind: "daemon", id: "daemon-1" },
    kind: "user_message",
    created_at: "2026-05-22T00:00:00.000Z",
    payload: { agent_id: "agent-1", message: "queued while offline" },
  });

  const offline = await runtime.handleFromClient(queued);
  assert.equal(offline.inserted, true);
  assert.equal(offline.idempotent, false);
  assert.equal(offline.delivery && "delivered" in offline.delivery ? offline.delivery.delivered : true, false);
  let saved = await store.getMessage("msg-offline");
  assert.equal(saved?.delivery_state, "pending");
  assert.equal(saved?.delivery_attempts, 0);

  const daemonSocket = new MemorySocket("daemon-conn-1");
  const attached = await runtime.attachDaemon("daemon-1", daemonSocket);
  assert.equal(attached.epoch, 1);
  assert.equal(attached.replayed, 1);
  assert.equal(attached.replay_failed, 0);
  assert.equal(daemonSocket.sent.find((envelope) => envelope.id === "msg-offline")?.payload.message, "queued while offline");
  saved = await store.getMessage("msg-offline");
  assert.equal(saved?.delivery_state, "delivered");
  assert.equal(saved?.delivery_attempts, 1);
  assert.equal(saved?.last_delivery_epoch, 1);

  const ack = makeAckEnvelope({
    id: "ack-offline",
    daemon_id: "daemon-1",
    source: { kind: "daemon", id: "daemon-1" },
    target: { kind: "relay", id: "test-relay" },
    ack_id: "msg-offline",
    ack_kind: "user_message",
    delivery_state: "acked",
    created_at: "2026-05-22T00:00:01.000Z",
  });
  const acked = await runtime.handleFromDaemon("daemon-conn-1", attached.epoch, ack);
  assert.equal(acked.acked?.delivery_state, "acked");
  saved = await store.getMessage("msg-offline");
  assert.equal(saved?.acked_at, "2026-05-22T00:00:01.000Z");

  const duplicate = await runtime.handleFromClient(queued);
  assert.equal(duplicate.inserted, false);
  assert.equal(duplicate.idempotent, true);
  assert.equal(duplicate.delivery && "reason" in duplicate.delivery ? duplicate.delivery.reason : "", "already_acked");
  assert.equal(daemonSocket.sent.filter((envelope) => envelope.id === "msg-offline").length, 1);
  await store.close();
});

test("RelayRuntime fences stale daemon epochs before broadcasting", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const coordinator = new StandaloneRegistryCoordinator();
  const runtime = new RelayRuntime(store, coordinator, { replayLimit: 5 });

  const oldDaemon = new MemorySocket("daemon-old");
  const oldAttach = await runtime.attachDaemon("daemon-1", oldDaemon);
  const newDaemon = new MemorySocket("daemon-new");
  const newAttach = await runtime.attachDaemon("daemon-1", newDaemon);
  assert.equal(oldAttach.epoch, 1);
  assert.equal(newAttach.epoch, 2);

  const client = new MemorySocket("client-1");
  await runtime.attachClient("daemon-1", "client-1", client);

  const staleEnvelope = makeRelayEnvelope({
    id: "msg-stale",
    daemon_id: "daemon-1",
    source: { kind: "daemon", id: "daemon-1" },
    target: { kind: "remote-client", id: "client-1" },
    kind: "agent_message",
    created_at: "2026-05-22T00:00:02.000Z",
    payload: { message: "from stale daemon" },
  });

  await assert.rejects(
    () => runtime.handleFromDaemon("daemon-old", oldAttach.epoch, staleEnvelope),
    /stale daemon connection/,
  );
  assert.equal(client.sent.some((envelope) => envelope.id === "msg-stale"), false);

  const currentEnvelope = makeRelayEnvelope({
    ...staleEnvelope,
    id: "msg-current",
    payload: { message: "from current daemon" },
  });
  const delivered = await runtime.handleFromDaemon("daemon-new", newAttach.epoch, currentEnvelope);
  assert.equal(delivered.delivery && "delivered" in delivered.delivery ? delivered.delivery.delivered : 0, 1);
  assert.equal(client.sent.some((envelope) => envelope.id === "msg-current"), true);
  await store.close();
});

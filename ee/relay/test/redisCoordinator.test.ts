import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { RelayError } from "../src/core/errors.js";
import { RelayRuntime } from "../src/core/runtime.js";
import { makeAckEnvelope, makeRelayEnvelope } from "../src/core/protocol.js";
import type { ClientAuthPrincipal, DaemonAuthPrincipal, RelayStore } from "../src/core/ports.js";
import { RedisRelayCoordinator } from "../src/adapters/standalone/redisCoordinator.js";
import { createStandaloneRelayServer } from "../src/adapters/standalone/server.js";
import { SqliteRelayStore } from "../src/adapters/standalone/sqliteStore.js";
import { createClientSessionFixture, createDaemonCredentialFixture, type DaemonCredentialFixture } from "./helpers/auth.js";
import { FakeRedis } from "./helpers/fakeRedis.js";
import { MemorySocket } from "./helpers/memorySocket.js";

function daemonPrincipal(fixture: DaemonCredentialFixture): DaemonAuthPrincipal {
  return {
    kind: "daemon",
    daemonId: fixture.daemonId,
    ownerUserId: fixture.ownerUserId,
    credentialId: fixture.credentialId,
    authMode: "signed-attach-v1",
  };
}

function clientPrincipal(args: { ownerUserId: string; sessionId: string }): ClientAuthPrincipal {
  return {
    kind: "client",
    ownerUserId: args.ownerUserId,
    sessionId: args.sessionId,
    userId: "user",
    authMode: "session-v1",
  };
}

function coordinator(args: {
  store: RelayStore;
  redis: FakeRedis;
  processId: string;
  leaseTtlMs?: number;
  renewIntervalMs?: number;
  disableAutoRenew?: boolean;
}): RedisRelayCoordinator {
  return new RedisRelayCoordinator({
    store: args.store,
    commands: args.redis,
    publisher: args.redis,
    subscriber: args.redis,
    processId: args.processId,
    leaseTtlMs: args.leaseTtlMs || 1_000,
    renewIntervalMs: args.renewIntervalMs || 100,
    requestTimeoutMs: 100,
    nowMs: () => args.redis.now,
    disableAutoRenew: args.disableAutoRenew ?? true,
  });
}

function toDaemonEnvelope(id: string, daemonId = "daemon-1") {
  return makeRelayEnvelope({
    id,
    daemon_id: daemonId,
    source: { kind: "remote-client", id: "client-1", user_id: "user" },
    target: { kind: "daemon", id: daemonId },
    kind: "user_message",
    created_at: "2026-05-23T00:00:00.000Z",
    payload: { message: id },
  });
}

function fromDaemonEnvelope(id: string, daemonId = "daemon-1") {
  return makeRelayEnvelope({
    id,
    daemon_id: daemonId,
    source: { kind: "daemon", id: daemonId },
    target: { kind: "remote-client", id: "client-1" },
    kind: "agent_message",
    created_at: "2026-05-23T00:00:01.000Z",
    payload: { message: id },
  });
}

test("RedisRelayCoordinator concurrent attach race has exactly one winner", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const fixture = await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
  const redis = new FakeRedis();
  const left = coordinator({ store, redis, processId: "left" });
  const right = coordinator({ store, redis, processId: "right" });
  const auth = daemonPrincipal(fixture);

  const [a, b] = await Promise.all([
    left.attachDaemon({ daemonId: "daemon-1", socket: new MemorySocket("daemon-left"), auth }),
    right.attachDaemon({ daemonId: "daemon-1", socket: new MemorySocket("daemon-right"), auth }),
  ]);
  const winners = [a, b].filter((result) => result.accepted);
  assert.equal(winners.length, 1);
  const winner = winners[0];
  const snapLeft = await left.snapshot("daemon-1");
  const snapRight = await right.snapshot("daemon-1");
  assert.equal(snapLeft.daemon_connection_id, winner.connectionId);
  assert.equal(snapRight.daemon_connection_id, winner.connectionId);
  assert.equal(snapLeft.epoch, winner.epoch);
  assert.equal((await store.getInstance("daemon-1"))?.fencing_epoch, winner.epoch);
  await left.close();
  await right.close();
  await store.close();
});

test("RedisRelayCoordinator lease expiry permits takeover and fences the old epoch", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const fixture = await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
  const redis = new FakeRedis();
  const oldCoordinator = coordinator({ store, redis, processId: "old", leaseTtlMs: 1_000 });
  const newCoordinator = coordinator({ store, redis, processId: "new", leaseTtlMs: 1_000 });
  const oldSocket = new MemorySocket("daemon-old");
  const oldAttach = await oldCoordinator.attachDaemon({ daemonId: "daemon-1", socket: oldSocket, auth: daemonPrincipal(fixture) });
  assert.equal(oldAttach.accepted, true);
  redis.advance(1_001);
  const newSocket = new MemorySocket("daemon-new");
  const newAttach = await newCoordinator.attachDaemon({ daemonId: "daemon-1", socket: newSocket, auth: daemonPrincipal(fixture) });
  assert.equal(newAttach.accepted, true);
  assert.equal(newAttach.epoch, oldAttach.epoch + 1);
  assert.equal(await oldCoordinator.isCurrentDaemonConnection("daemon-1", "daemon-old", oldAttach.epoch), false);

  const runtime = new RelayRuntime(store, oldCoordinator);
  await assert.rejects(
    () => runtime.handleFromDaemon("daemon-old", oldAttach.epoch, fromDaemonEnvelope("stale-after-expiry")),
    /stale daemon connection/,
  );
  await oldCoordinator.sendToDaemon("daemon-1", toDaemonEnvelope("after-takeover"));
  assert.equal(oldSocket.sent.length, 0, "old owner must not receive post-handover delivery");
  assert.equal(newSocket.sent.some((envelope) => envelope.id === "after-takeover"), true);
  await oldCoordinator.close();
  await newCoordinator.close();
  await store.close();
});

test("RedisRelayCoordinator owner change propagates through live store revalidation", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const ownerOne = await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-owner-1" });
  const redis = new FakeRedis();
  const ownerOneCoordinator = coordinator({ store, redis, processId: "owner-one", disableAutoRenew: false, renewIntervalMs: 20 });
  const ownerOneSocket = new MemorySocket("daemon-owner-one");
  const first = await ownerOneCoordinator.attachDaemon({ daemonId: "daemon-1", socket: ownerOneSocket, auth: daemonPrincipal(ownerOne) });
  assert.equal(first.accepted, true);

  const ownerTwo = await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-2", credentialId: "cred-owner-2" });
  await waitFor(() => Promise.resolve(ownerOneSocket.closes.some((close) => close.reason === "authenticated_session_revoked")));
  assert.equal(await ownerOneCoordinator.isCurrentDaemonConnection("daemon-1", "daemon-owner-one", first.epoch), false);

  const ownerTwoCoordinator = coordinator({ store, redis, processId: "owner-two" });
  const second = await ownerTwoCoordinator.attachDaemon({ daemonId: "daemon-1", socket: new MemorySocket("daemon-owner-two"), auth: daemonPrincipal(ownerTwo) });
  assert.equal(second.accepted, true);
  assert.equal((await store.getInstance("daemon-1"))?.owner_user_id, "owner-2");
  await ownerOneCoordinator.close();
  await ownerTwoCoordinator.close();
  await store.close();
});

test("RedisRelayCoordinator rejects stale token after a newer epoch", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const fixture = await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
  const redis = new FakeRedis();
  const firstCoordinator = coordinator({ store, redis, processId: "first" });
  const first = await firstCoordinator.attachDaemon({ daemonId: "daemon-1", socket: new MemorySocket("daemon-first"), auth: daemonPrincipal(fixture) });
  assert.equal(first.accepted, true);
  await firstCoordinator.detach("daemon-first");
  const secondCoordinator = coordinator({ store, redis, processId: "second" });
  const second = await secondCoordinator.attachDaemon({ daemonId: "daemon-1", socket: new MemorySocket("daemon-second"), auth: daemonPrincipal(fixture) });
  assert.equal(second.accepted, true);
  assert.equal(second.epoch, first.epoch + 1);
  assert.equal(await firstCoordinator.isCurrentDaemonConnection("daemon-1", "daemon-first", first.epoch), false);
  const staleClaim = await store.claimInstanceOwner({
    id: "daemon-1",
    ownerUserId: "owner-1",
    credentialId: "cred-1",
    fencingEpoch: first.epoch,
  });
  assert.equal(staleClaim.claimed, false);
  assert.equal(staleClaim.reason, "stale_fencing_epoch");
  assert.equal((await store.getInstance("daemon-1"))?.fencing_epoch, second.epoch);
  await firstCoordinator.close();
  await secondCoordinator.close();
  await store.close();
});

test("RedisRelayCoordinator routes remote-to-daemon and daemon-to-client across processes", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const fixture = await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
  const session = await createClientSessionFixture(store, { ownerUserId: "owner-1", sessionId: "session-1", token: "client-token" });
  const redis = new FakeRedis();
  const daemonProcess = coordinator({ store, redis, processId: "daemon-process" });
  const clientProcess = coordinator({ store, redis, processId: "client-process" });
  const daemonSocket = new MemorySocket("daemon-socket");
  const attach = await daemonProcess.attachDaemon({ daemonId: "daemon-1", socket: daemonSocket, auth: daemonPrincipal(fixture) });
  assert.equal(attach.accepted, true);
  const clientSocket = new MemorySocket("client-socket");
  const clientAttach = await clientProcess.attachClient({
    daemonId: "daemon-1",
    clientId: "client-1",
    socket: clientSocket,
    auth: clientPrincipal({ ownerUserId: "owner-1", sessionId: session.sessionId }),
  });
  assert.equal(clientAttach.accepted, true);

  const toDaemon = await clientProcess.sendToDaemon("daemon-1", toDaemonEnvelope("cross-to-daemon"));
  assert.equal(toDaemon.delivered, true);
  assert.equal(toDaemon.epoch, attach.epoch);
  assert.equal(daemonSocket.sent.some((envelope) => envelope.id === "cross-to-daemon"), true);

  const toClients = await daemonProcess.broadcastToClients("daemon-1", fromDaemonEnvelope("cross-to-client"));
  assert.equal(toClients.delivered, 1);
  assert.deepEqual(toClients.connectionIds, ["client-socket"]);
  assert.equal(clientSocket.sent.some((envelope) => envelope.id === "cross-to-client"), true);
  await daemonProcess.close();
  await clientProcess.close();
  await store.close();
});

test("RelayRuntime replay stops when Redis lease is lost mid-attach", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const fixture = await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
  await store.appendMessage(toDaemonEnvelope("queued-1"), "to_daemon");
  await store.appendMessage(toDaemonEnvelope("queued-2"), "to_daemon");
  const redis = new FakeRedis();
  const redisCoordinator = coordinator({ store, redis, processId: "relay", leaseTtlMs: 1_000 });
  class ExpiringSocket extends MemorySocket {
    override sendEnvelope(envelope: ReturnType<typeof toDaemonEnvelope>): void {
      super.sendEnvelope(envelope);
      redis.advance(1_001);
    }
  }
  const runtime = new RelayRuntime(store, redisCoordinator, { replayLimit: 10 });
  const attached = await runtime.attachDaemon("daemon-1", new ExpiringSocket("daemon-replay"), daemonPrincipal(fixture));
  assert.equal(attached.accepted, true);
  assert.equal(attached.replayed, 1);
  assert.equal(attached.replay_failed, 0);
  assert.equal((await store.getMessage("queued-1"))?.delivery_state, "delivered");
  assert.equal((await store.getMessage("queued-2"))?.delivery_state, "pending");
  const staleAck = makeAckEnvelope({
    id: "ack-stale",
    daemon_id: "daemon-1",
    source: { kind: "daemon", id: "daemon-1" },
    target: { kind: "relay", id: "relay" },
    ack_id: "queued-2",
    ack_kind: "user_message",
  });
  await assert.rejects(() => runtime.handleFromDaemon("daemon-replay", attached.epoch, staleAck), /stale daemon connection/);
  await redisCoordinator.close();
  await store.close();
});

test("Redis restart flush reseeds epoch from relay_instances fencing_epoch", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const fixture = await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
  const redis = new FakeRedis();
  const beforeFlush = coordinator({ store, redis, processId: "before-flush" });
  const first = await beforeFlush.attachDaemon({ daemonId: "daemon-1", socket: new MemorySocket("daemon-before"), auth: daemonPrincipal(fixture) });
  assert.equal(first.accepted, true);
  assert.equal((await store.getInstance("daemon-1"))?.fencing_epoch, first.epoch);

  redis.flushAll();
  assert.equal(await beforeFlush.isCurrentDaemonConnection("daemon-1", "daemon-before", first.epoch), false);

  const afterFlush = coordinator({ store, redis, processId: "after-flush" });
  const second = await afterFlush.attachDaemon({ daemonId: "daemon-1", socket: new MemorySocket("daemon-after"), auth: daemonPrincipal(fixture) });
  assert.equal(second.accepted, true);
  assert.ok(second.epoch > first.epoch, `expected reseeded epoch > ${first.epoch}, got ${second.epoch}`);
  assert.equal((await store.getInstance("daemon-1"))?.fencing_epoch, second.epoch);
  await beforeFlush.close();
  await afterFlush.close();
  await store.close();
});

test("standalone Redis config is opt-in and fail-fast when explicitly unsafe/unavailable", async () => {
  const local = await createStandaloneRelayServer({ port: 0, databasePath: ":memory:" });
  assert.equal(local.coordinator.kind, "standalone-registry");
  await local.store.close();

  await assert.rejects(
    () => createStandaloneRelayServer({ port: 0, databasePath: ":memory:", coordination: "redis" }),
    /TORQUE_RELAY_REDIS_URL is required/,
  );
  await assert.rejects(
    () => createStandaloneRelayServer({ port: 0, databasePath: ":memory:", redisUrl: "redis://127.0.0.1:1" }),
    /requires a shared durable TORQUE_RELAY_DB/,
  );

  const dir = await mkdtemp(join(tmpdir(), "torque-redis-unavailable-"));
  try {
    await assert.rejects(
      () => createStandaloneRelayServer({ port: 0, databasePath: join(dir, "relay.db"), redisUrl: "redis://127.0.0.1:1" }),
      (error) => error instanceof RelayError && error.code === "redis_connect_failed",
    );
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

async function waitFor(predicate: () => Promise<boolean>, timeoutMs = 1_000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error("timed out waiting for condition");
}

import assert from "node:assert/strict";
import test from "node:test";

import { makeRelayEnvelope, type RelayEnvelope } from "../src/core/protocol.js";
import type { RelaySocket } from "../src/core/ports.js";
import { StandaloneRegistryCoordinator } from "../src/adapters/standalone/registryCoordinator.js";

class MemorySocket implements RelaySocket {
  readonly sent: RelayEnvelope[] = [];
  readonly closes: { code?: number; reason?: string }[] = [];
  constructor(readonly id: string) {}
  sendEnvelope(envelope: RelayEnvelope): void {
    this.sent.push(envelope);
  }
  close(code?: number, reason?: string): void {
    this.closes.push({ code, reason });
  }
}

test("StandaloneRegistryCoordinator enforces one active daemon owner and routes through it", async () => {
  const coordinator = new StandaloneRegistryCoordinator();
  const daemonOne = new MemorySocket("daemon-conn-1");
  const first = await coordinator.attachDaemon({ daemonId: "daemon-1", socket: daemonOne });
  assert.equal(first.accepted, true);
  assert.equal(first.epoch, 1);

  const clientOne = new MemorySocket("client-conn-1");
  await coordinator.attachClient({ daemonId: "daemon-1", clientId: "client-1", socket: clientOne });

  const toDaemon = makeRelayEnvelope({
    id: "msg-client",
    daemon_id: "daemon-1",
    source: { kind: "remote-client", id: "client-1" },
    target: { kind: "daemon", id: "daemon-1" },
    kind: "user_message",
    payload: { message: "hello" },
  });
  assert.equal(await coordinator.sendToDaemon("daemon-1", toDaemon), true);
  assert.equal(daemonOne.sent[0].id, "msg-client");

  const fromDaemon = makeRelayEnvelope({
    id: "msg-daemon",
    daemon_id: "daemon-1",
    source: { kind: "daemon", id: "daemon-1" },
    target: { kind: "remote-client", id: "client-1" },
    kind: "agent_message",
    payload: { message: "hi" },
  });
  assert.equal(await coordinator.broadcastToClients("daemon-1", fromDaemon), 1);
  assert.equal(clientOne.sent[0].id, "msg-daemon");

  const daemonTwo = new MemorySocket("daemon-conn-2");
  const second = await coordinator.attachDaemon({ daemonId: "daemon-1", socket: daemonTwo });
  assert.equal(second.epoch, 2);
  assert.equal(second.replacedConnectionId, "daemon-conn-1");
  assert.deepEqual(daemonOne.closes, [{ code: 4000, reason: "replaced_by_new_daemon_connection" }]);

  const afterReplace = makeRelayEnvelope({
    id: "msg-after-replace",
    daemon_id: "daemon-1",
    source: { kind: "remote-client", id: "client-1" },
    target: { kind: "daemon", id: "daemon-1" },
    kind: "user_message",
    payload: { message: "new owner" },
  });
  assert.equal(await coordinator.sendToDaemon("daemon-1", afterReplace), true);
  assert.equal(daemonOne.sent.length, 1, "stale daemon should not receive after replacement");
  assert.equal(daemonTwo.sent[0].id, "msg-after-replace");

  const snapshot = await coordinator.snapshot("daemon-1");
  assert.equal(snapshot.daemon_online, true);
  assert.equal(snapshot.daemon_connection_id, "daemon-conn-2");
  assert.deepEqual(snapshot.client_connection_ids, ["client-conn-1"]);

  coordinator.detach("daemon-conn-2");
  assert.equal((await coordinator.snapshot("daemon-1")).daemon_online, false);
});

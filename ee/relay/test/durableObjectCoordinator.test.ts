import assert from "node:assert/strict";
import test from "node:test";

import { makeRelayEnvelope, parseRelayEnvelopeJson, type RelayEnvelope } from "../src/core/protocol.js";
import {
  DaemonRendezvousDurableObject,
  type DurableObjectSessionAttachment,
} from "../src/adapters/cloudflare/durableObjectCoordinator.js";

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
  const oldDaemon = new FakeCfWebSocket({
    role: "daemon",
    daemonId: "daemon-1",
    clientId: "",
    connectionId: "daemon-old",
    epoch: 0,
  });
  const newDaemon = new FakeCfWebSocket({
    role: "daemon",
    daemonId: "daemon-1",
    clientId: "",
    connectionId: "daemon-new",
    epoch: 0,
  });
  const client = new FakeCfWebSocket({
    role: "client",
    daemonId: "daemon-1",
    clientId: "client-1",
    connectionId: "client-1",
    epoch: 0,
  });
  const state = new FakeDurableObjectState([oldDaemon, newDaemon, client]);
  const durableObject = new DaemonRendezvousDurableObject(state as unknown as DurableObjectState, {});

  await durableObject.rehydrateHibernatedSocketsForTest();

  assert.equal(oldDaemon.closes.some((close) => close.code === 4000 && close.reason === "replaced_by_new_daemon_connection"), true);
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
  assert.equal(newDaemon.envelopes().some((envelope) => envelope.id === "msg-client"), true);
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

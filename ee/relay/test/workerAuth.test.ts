import assert from "node:assert/strict";
import test from "node:test";

import worker from "../src/adapters/cloudflare/worker.js";
import { D1RelayStore } from "../src/adapters/cloudflare/d1Store.js";
import { FakeD1Database } from "./helpers/fakeD1.js";
import { createClientSessionFixture } from "./helpers/auth.js";
import { makeRelayEnvelope } from "../src/core/protocol.js";

class FakeNamespace {
  idFromName(name: string): string { return name; }
  get(_id: string): { fetch(request: Request): Promise<Response> } {
    return { fetch: async () => new Response(JSON.stringify({ type: "ok", delivery: { delivered: false, epoch: 0 } }), { headers: { "content-type": "application/json" } }) };
  }
}

test("Cloudflare Worker HTTP enqueue rejects unauthenticated clients before D1 append", async () => {
  const fake = new FakeD1Database();
  const store = new D1RelayStore(fake as unknown as D1Database);
  await store.migrate();
  await store.upsertInstance({
    id: "daemon-1",
    owner_user_id: "owner-1",
    label: "Daemon",
    created_at: "2026-05-23T00:00:00.000Z",
    last_seen_at: "2026-05-23T00:00:00.000Z",
    fencing_epoch: 0,
    active_credential_id: "",
    coordination_updated_at: "2026-05-23T00:00:00.000Z",
    metadata: {},
  });
  const envelope = makeRelayEnvelope({
    id: "msg-unauth-worker",
    daemon_id: "daemon-1",
    source: { kind: "remote-client", id: "client-1", user_id: "evil" },
    target: { kind: "daemon", id: "daemon-1" },
    kind: "user_message",
    created_at: "2026-05-23T00:00:00.000Z",
    payload: { agent_id: "agent-1", message: "hi" },
  });
  const response = await worker.fetch(new Request("https://relay.example.com/v1/messages/daemon-1", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(envelope),
  }), { RELAY_DB: fake as unknown as D1Database, RENDEZVOUS: new FakeNamespace() as any });
  assert.equal(response.status, 401);
  assert.equal(await store.getMessage("msg-unauth-worker"), null);
  fake.close();
});

test("Cloudflare Worker HTTP enqueue accepts owner session and forces V1 source.user_id", async () => {
  const fake = new FakeD1Database();
  const store = new D1RelayStore(fake as unknown as D1Database);
  await store.migrate();
  await store.upsertInstance({
    id: "daemon-1",
    owner_user_id: "owner-1",
    label: "Daemon",
    created_at: "2026-05-23T00:00:00.000Z",
    last_seen_at: "2026-05-23T00:00:00.000Z",
    fencing_epoch: 0,
    active_credential_id: "",
    coordination_updated_at: "2026-05-23T00:00:00.000Z",
    metadata: {},
  });
  const session = await createClientSessionFixture(store, { ownerUserId: "owner-1", token: "client-token" });
  const envelope = makeRelayEnvelope({
    id: "msg-auth-worker",
    daemon_id: "daemon-1",
    source: { kind: "remote-client", id: "client-1", user_id: "evil" },
    target: { kind: "daemon", id: "daemon-1" },
    kind: "user_message",
    created_at: "2026-05-23T00:00:00.000Z",
    payload: { agent_id: "agent-1", message: "hi" },
  });
  const response = await worker.fetch(new Request("https://relay.example.com/v1/messages/daemon-1", {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${session.token}` },
    body: JSON.stringify(envelope),
  }), { RELAY_DB: fake as unknown as D1Database, RENDEZVOUS: new FakeNamespace() as any });
  assert.equal(response.status, 202);
  const saved = await store.getMessage("msg-auth-worker");
  assert.equal(saved?.source.user_id, "user");
  fake.close();
});

import assert from "node:assert/strict";
import test from "node:test";

import { makeRelayEnvelope } from "../src/core/protocol.js";
import { RelayConflictError } from "../src/core/errors.js";
import { D1RelayStore } from "../src/adapters/cloudflare/d1Store.js";
import { FakeD1Database } from "./helpers/fakeD1.js";

test("D1RelayStore uses the same RelayStore contract as SQLite", async () => {
  const fake = new FakeD1Database();
  const store = new D1RelayStore(fake as unknown as D1Database);
  await store.migrate();
  await store.upsertInstance({
    id: "daemon-d1",
    owner_user_id: "user-1",
    label: "D1 daemon",
    created_at: "2026-05-22T00:00:00.000Z",
    last_seen_at: "2026-05-22T00:00:01.000Z",
    fencing_epoch: 4,
    active_credential_id: "cred-d1",
    coordination_updated_at: "2026-05-22T00:00:01.000Z",
    metadata: { target: "cloudflare" },
  });
  assert.equal((await store.getInstance("daemon-d1"))?.metadata.target, "cloudflare");
  assert.equal((await store.getInstance("daemon-d1"))?.fencing_epoch, 4);

  const envelope = makeRelayEnvelope({
    id: "msg-d1",
    daemon_id: "daemon-d1",
    source: { kind: "channel", id: "slack:C123", platform: "slack" },
    target: { kind: "daemon", id: "daemon-d1" },
    kind: "channel_event",
    created_at: "2026-05-22T00:00:02.000Z",
    payload: { text: "remote hello" },
  });
  const first = await store.appendMessageResult(envelope, "channel_ingress");
  assert.equal(first.inserted, true);
  const second = await store.appendMessageResult(envelope, "channel_ingress");
  assert.equal(second.idempotent, true);
  const messages = await store.listMessages("daemon-d1", { direction: "channel_ingress" });
  assert.equal(messages.length, 1);
  assert.equal(messages[0].source.platform, "slack");
  assert.deepEqual(messages[0].payload, { text: "remote hello" });

  await assert.rejects(
    () => store.appendMessage(makeRelayEnvelope({
      id: "msg-d1",
      daemon_id: "daemon-d1",
      source: { kind: "channel", id: "slack:C123", platform: "slack" },
      target: { kind: "daemon", id: "daemon-d1" },
      kind: "channel_event",
      created_at: "2026-05-22T00:00:02.000Z",
      payload: { text: "different" },
    }), "channel_ingress"),
    RelayConflictError,
  );
  fake.close();
});

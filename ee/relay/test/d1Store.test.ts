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

test("D1RelayStore claimInstanceOwner mirrors owner-CAS and fencing semantics", async () => {
  const fake = new FakeD1Database();
  const store = new D1RelayStore(fake as unknown as D1Database);
  await store.migrate();
  const first = await store.claimInstanceOwner({
    id: "daemon-cas-d1",
    ownerUserId: "owner-1",
    credentialId: "cred-1",
    fencingEpoch: 1,
    now: "2026-05-23T00:00:00.000Z",
  });
  assert.equal(first.claimed, true);
  const second = await store.claimInstanceOwner({
    id: "daemon-cas-d1",
    ownerUserId: "owner-1",
    credentialId: "cred-1",
    fencingEpoch: 2,
  });
  assert.equal(second.claimed, true);
  const stale = await store.claimInstanceOwner({
    id: "daemon-cas-d1",
    ownerUserId: "owner-1",
    credentialId: "cred-1",
    fencingEpoch: 1,
  });
  assert.equal(stale.claimed, false);
  assert.equal(stale.reason, "stale_fencing_epoch");
  const wrongOwner = await store.claimInstanceOwner({
    id: "daemon-cas-d1",
    ownerUserId: "owner-2",
    credentialId: "cred-2",
    fencingEpoch: 3,
  });
  assert.equal(wrongOwner.claimed, false);
  assert.equal(wrongOwner.reason, "daemon_owner_mismatch");
  assert.equal((await store.getInstance("daemon-cas-d1"))?.fencing_epoch, 2);
  fake.close();
});

test("D1RelayStore upsertInstance preserves monotonic fencing_epoch", async () => {
  const fake = new FakeD1Database();
  const store = new D1RelayStore(fake as unknown as D1Database);
  await store.migrate();
  await store.claimInstanceOwner({
    id: "daemon-rotation-d1",
    ownerUserId: "owner-1",
    credentialId: "cred-1",
    fencingEpoch: 7,
    now: "2026-05-23T00:00:00.000Z",
  });

  const rotated = await store.upsertInstance({
    id: "daemon-rotation-d1",
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
  assert.equal((await store.getInstance("daemon-rotation-d1"))?.fencing_epoch, 7);
  fake.close();
});

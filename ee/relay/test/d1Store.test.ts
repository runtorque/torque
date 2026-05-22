import assert from "node:assert/strict";
import test from "node:test";
// @ts-ignore -- node:sqlite exists at runtime on Node >=22.5.
import { DatabaseSync } from "node:sqlite";

import { makeRelayEnvelope } from "../src/core/protocol.js";
import { D1RelayStore } from "../src/adapters/cloudflare/d1Store.js";

class FakeD1Statement {
  constructor(private readonly db: any, private readonly sql: string, private readonly params: unknown[] = []) {}
  bind(...params: unknown[]): FakeD1Statement {
    return new FakeD1Statement(this.db, this.sql, params);
  }
  async run(): Promise<{ success: true }> {
    this.db.prepare(this.sql).run(...this.params);
    return { success: true };
  }
  async first<T>(): Promise<T | null> {
    return (this.db.prepare(this.sql).get(...this.params) || null) as T | null;
  }
  async all<T>(): Promise<{ results: T[]; success: true }> {
    return { results: this.db.prepare(this.sql).all(...this.params) as T[], success: true };
  }
}

class FakeD1Database {
  readonly db = new DatabaseSync(":memory:");
  prepare(sql: string): FakeD1Statement {
    return new FakeD1Statement(this.db, sql);
  }
  close(): void {
    this.db.close();
  }
}

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
    metadata: { target: "cloudflare" },
  });
  assert.equal((await store.getInstance("daemon-d1"))?.metadata.target, "cloudflare");

  const envelope = makeRelayEnvelope({
    id: "msg-d1",
    daemon_id: "daemon-d1",
    source: { kind: "channel", id: "slack:C123", platform: "slack" },
    target: { kind: "daemon", id: "daemon-d1" },
    kind: "channel_event",
    created_at: "2026-05-22T00:00:02.000Z",
    payload: { text: "remote hello" },
  });
  await store.appendMessage(envelope, "channel_ingress");
  const messages = await store.listMessages("daemon-d1", { direction: "channel_ingress" });
  assert.equal(messages.length, 1);
  assert.equal(messages[0].source.platform, "slack");
  assert.deepEqual(messages[0].payload, { text: "remote hello" });
  fake.close();
});

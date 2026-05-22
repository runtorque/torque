// node:sqlite ships with modern Node. The @types surface can lag current Node,
// so keep this import localized to the standalone adapter.
// @ts-ignore -- node:sqlite exists at runtime on Node >=22.5.
import { DatabaseSync } from "node:sqlite";

import type { RelayEnvelope } from "../../core/protocol.js";
import type {
  ListRelayMessagesOptions,
  RelayDirection,
  RelayInstanceRecord,
  RelayStore,
  StoredRelayMessage,
} from "../../core/ports.js";
import {
  RELAY_SCHEMA_STATEMENTS,
  clampRelayLimit,
  normalizeRelayInstanceRow,
  normalizeRelayMessageRow,
  relayInstanceValues,
  relayMessageValues,
  type RelayInstanceRow,
  type RelayMessageRow,
} from "../../core/sql.js";

type DatabaseSyncLike = {
  exec(sql: string): void;
  prepare(sql: string): {
    run(...args: unknown[]): unknown;
    get(...args: unknown[]): unknown;
    all(...args: unknown[]): unknown[];
  };
  close(): void;
};

export class SqliteRelayStore implements RelayStore {
  readonly kind = "sqlite";
  private readonly db: DatabaseSyncLike;

  constructor(path = ":memory:") {
    this.db = new DatabaseSync(path) as DatabaseSyncLike;
  }

  async migrate(): Promise<void> {
    for (const statement of RELAY_SCHEMA_STATEMENTS) {
      this.db.exec(statement);
    }
  }

  async upsertInstance(record: RelayInstanceRecord): Promise<RelayInstanceRecord> {
    this.db.prepare(
      `INSERT INTO relay_instances
        (id, owner_user_id, label, created_at, last_seen_at, metadata_json)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
        owner_user_id=excluded.owner_user_id,
        label=excluded.label,
        last_seen_at=excluded.last_seen_at,
        metadata_json=excluded.metadata_json`,
    ).run(...relayInstanceValues(record));
    const saved = await this.getInstance(record.id);
    if (!saved) throw new Error(`failed to save relay instance ${record.id}`);
    return saved;
  }

  async getInstance(id: string): Promise<RelayInstanceRecord | null> {
    const row = this.db.prepare(
      `SELECT id, owner_user_id, label, created_at, last_seen_at, metadata_json
       FROM relay_instances WHERE id=?`,
    ).get(String(id || "").trim()) as RelayInstanceRow | undefined;
    return normalizeRelayInstanceRow(row);
  }

  async appendMessage(envelope: RelayEnvelope, direction: RelayDirection): Promise<StoredRelayMessage> {
    this.db.prepare(
      `INSERT OR IGNORE INTO relay_messages
        (id, daemon_id, direction, kind, source_json, target_json, payload_json,
         created_at, delivered_at, acked_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).run(...relayMessageValues(envelope, direction));
    const saved = await this.getMessage(envelope.id);
    if (!saved) throw new Error(`failed to save relay message ${envelope.id}`);
    return saved;
  }

  async listMessages(
    daemonId: string,
    options: ListRelayMessagesOptions = {},
  ): Promise<StoredRelayMessage[]> {
    const clauses = ["daemon_id=?"];
    const params: unknown[] = [String(daemonId || "").trim()];
    if (options.direction) {
      clauses.push("direction=?");
      params.push(options.direction);
    }
    if (options.since_created_at) {
      clauses.push("created_at>?");
      params.push(options.since_created_at);
    }
    params.push(clampRelayLimit(options.limit));
    const rows = this.db.prepare(
      `SELECT id, daemon_id, direction, kind, source_json, target_json, payload_json,
              created_at, delivered_at, acked_at
       FROM relay_messages
       WHERE ${clauses.join(" AND ")}
       ORDER BY created_at ASC, id ASC
       LIMIT ?`,
    ).all(...params) as RelayMessageRow[];
    return rows.map((row) => normalizeRelayMessageRow(row)).filter(isStoredMessage);
  }

  async markDelivered(messageId: string, deliveredAt = new Date().toISOString()): Promise<StoredRelayMessage | null> {
    this.db.prepare(
      `UPDATE relay_messages SET delivered_at=? WHERE id=?`,
    ).run(deliveredAt, String(messageId || "").trim());
    return this.getMessage(messageId);
  }

  async markAcked(messageId: string, ackedAt = new Date().toISOString()): Promise<StoredRelayMessage | null> {
    this.db.prepare(
      `UPDATE relay_messages SET acked_at=? WHERE id=?`,
    ).run(ackedAt, String(messageId || "").trim());
    return this.getMessage(messageId);
  }

  async close(): Promise<void> {
    this.db.close();
  }

  private async getMessage(messageId: string): Promise<StoredRelayMessage | null> {
    const row = this.db.prepare(
      `SELECT id, daemon_id, direction, kind, source_json, target_json, payload_json,
              created_at, delivered_at, acked_at
       FROM relay_messages WHERE id=?`,
    ).get(String(messageId || "").trim()) as RelayMessageRow | undefined;
    return normalizeRelayMessageRow(row);
  }
}

function isStoredMessage(value: StoredRelayMessage | null): value is StoredRelayMessage {
  return Boolean(value);
}

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

export class D1RelayStore implements RelayStore {
  readonly kind = "d1";

  constructor(private readonly db: D1Database) {}

  async migrate(): Promise<void> {
    for (const statement of RELAY_SCHEMA_STATEMENTS) {
      await this.db.prepare(statement).run();
    }
  }

  async upsertInstance(record: RelayInstanceRecord): Promise<RelayInstanceRecord> {
    await this.db.prepare(
      `INSERT INTO relay_instances
        (id, owner_user_id, label, created_at, last_seen_at, metadata_json)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
        owner_user_id=excluded.owner_user_id,
        label=excluded.label,
        last_seen_at=excluded.last_seen_at,
        metadata_json=excluded.metadata_json`,
    ).bind(...relayInstanceValues(record)).run();
    const saved = await this.getInstance(record.id);
    if (!saved) throw new Error(`failed to save relay instance ${record.id}`);
    return saved;
  }

  async getInstance(id: string): Promise<RelayInstanceRecord | null> {
    const row = await this.db.prepare(
      `SELECT id, owner_user_id, label, created_at, last_seen_at, metadata_json
       FROM relay_instances WHERE id=?`,
    ).bind(String(id || "").trim()).first<RelayInstanceRow>();
    return normalizeRelayInstanceRow(row);
  }

  async appendMessage(envelope: RelayEnvelope, direction: RelayDirection): Promise<StoredRelayMessage> {
    await this.db.prepare(
      `INSERT OR IGNORE INTO relay_messages
        (id, daemon_id, direction, kind, source_json, target_json, payload_json,
         created_at, delivered_at, acked_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(...relayMessageValues(envelope, direction)).run();
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
    const result = await this.db.prepare(
      `SELECT id, daemon_id, direction, kind, source_json, target_json, payload_json,
              created_at, delivered_at, acked_at
       FROM relay_messages
       WHERE ${clauses.join(" AND ")}
       ORDER BY created_at ASC, id ASC
       LIMIT ?`,
    ).bind(...params).all<RelayMessageRow>();
    const rows = Array.isArray(result.results) ? result.results : [];
    return rows.map((row) => normalizeRelayMessageRow(row)).filter(isStoredMessage);
  }

  async markDelivered(messageId: string, deliveredAt = new Date().toISOString()): Promise<StoredRelayMessage | null> {
    await this.db.prepare(
      `UPDATE relay_messages SET delivered_at=? WHERE id=?`,
    ).bind(deliveredAt, String(messageId || "").trim()).run();
    return this.getMessage(messageId);
  }

  async markAcked(messageId: string, ackedAt = new Date().toISOString()): Promise<StoredRelayMessage | null> {
    await this.db.prepare(
      `UPDATE relay_messages SET acked_at=? WHERE id=?`,
    ).bind(ackedAt, String(messageId || "").trim()).run();
    return this.getMessage(messageId);
  }

  private async getMessage(messageId: string): Promise<StoredRelayMessage | null> {
    const row = await this.db.prepare(
      `SELECT id, daemon_id, direction, kind, source_json, target_json, payload_json,
              created_at, delivered_at, acked_at
       FROM relay_messages WHERE id=?`,
    ).bind(String(messageId || "").trim()).first<RelayMessageRow>();
    return normalizeRelayMessageRow(row);
  }
}

function isStoredMessage(value: StoredRelayMessage | null): value is StoredRelayMessage {
  return Boolean(value);
}

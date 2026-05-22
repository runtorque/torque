// node:sqlite ships with modern Node. The @types surface can lag current Node,
// so keep this import localized to the standalone adapter.
// @ts-ignore -- node:sqlite exists at runtime on Node >=22.5.
import { DatabaseSync } from "node:sqlite";

import type { RelayEnvelope } from "../../core/protocol.js";
import { RelayConflictError, RelayStoreError, errorMessage } from "../../core/errors.js";
import type {
  AppendMessageResult,
  ListRelayMessagesOptions,
  PendingRelayMessagesOptions,
  RelayDirection,
  RelayInstanceRecord,
  RelayStore,
  StoredRelayMessage,
} from "../../core/ports.js";
import {
  RELAY_MESSAGE_COLUMNS,
  RELAY_MESSAGE_SELECT,
  RELAY_SCHEMA_STATEMENTS,
  clampRelayLimit,
  normalizeRelayInstanceRow,
  normalizeRelayMessageRow,
  nowIso,
  relayEnvelopeHash,
  relayInstanceValues,
  relayMessageValues,
  type RelayInstanceRow,
  type RelayMessageRow,
} from "../../core/sql.js";

type StatementSyncLike = {
  run(...args: unknown[]): unknown;
  get(...args: unknown[]): unknown;
  all(...args: unknown[]): unknown[];
};

type DatabaseSyncLike = {
  exec(sql: string): void;
  prepare(sql: string): StatementSyncLike;
  close(): void;
};

export class SqliteRelayStore implements RelayStore {
  readonly kind = "sqlite";
  private readonly db: DatabaseSyncLike;
  private closed = false;

  constructor(path = ":memory:") {
    this.db = new DatabaseSync(path) as DatabaseSyncLike;
  }

  async migrate(): Promise<void> {
    return this.operation("migrate", () => {
      for (const statement of RELAY_SCHEMA_STATEMENTS) {
        this.db.exec(statement);
      }
    });
  }

  async upsertInstance(record: RelayInstanceRecord): Promise<RelayInstanceRecord> {
    return this.operation("upsertInstance", () => {
      assertNonEmpty(record.id, "instance id");
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
      const saved = this.getInstanceSync(record.id);
      if (!saved) throw new RelayStoreError(`failed to save relay instance ${record.id}`);
      return saved;
    });
  }

  async getInstance(id: string): Promise<RelayInstanceRecord | null> {
    return this.operation("getInstance", () => this.getInstanceSync(id));
  }

  async appendMessage(envelope: RelayEnvelope, direction: RelayDirection): Promise<StoredRelayMessage> {
    return (await this.appendMessageResult(envelope, direction)).message;
  }

  async appendMessageResult(envelope: RelayEnvelope, direction: RelayDirection): Promise<AppendMessageResult> {
    return this.operation("appendMessage", () => {
      const expectedHash = relayEnvelopeHash(envelope);
      this.db.prepare(
        `INSERT OR IGNORE INTO relay_messages
          (${RELAY_MESSAGE_COLUMNS.join(", ")})
         VALUES (${RELAY_MESSAGE_COLUMNS.map(() => "?").join(", ")})`,
      ).run(...relayMessageValues(envelope, direction));
      const inserted = this.lastChanges() > 0;
      const saved = this.getMessageSync(envelope.id, { idempotent: !inserted });
      if (!saved) throw new RelayStoreError(`failed to save relay message ${envelope.id}`);
      if (saved.envelope_hash !== expectedHash || saved.direction !== direction) {
        throw new RelayConflictError(`relay message id conflict for ${envelope.id}`);
      }
      return { message: saved, inserted, idempotent: !inserted };
    });
  }

  async getMessage(messageId: string): Promise<StoredRelayMessage | null> {
    return this.operation("getMessage", () => this.getMessageSync(messageId));
  }

  async listMessages(
    daemonId: string,
    options: ListRelayMessagesOptions = {},
  ): Promise<StoredRelayMessage[]> {
    return this.operation("listMessages", () => {
      const clauses = ["daemon_id=?"];
      const params: unknown[] = [cleanDaemonId(daemonId)];
      if (options.direction) {
        clauses.push("direction=?");
        params.push(options.direction);
      }
      if (options.since_created_at) {
        clauses.push("created_at>?");
        params.push(options.since_created_at);
      }
      if (options.include_acked === false) {
        clauses.push("acked_at=''");
      }
      params.push(clampRelayLimit(options.limit));
      const rows = this.db.prepare(
        `SELECT ${RELAY_MESSAGE_SELECT}
         FROM relay_messages
         WHERE ${clauses.join(" AND ")}
         ORDER BY created_at ASC, id ASC
         LIMIT ?`,
      ).all(...params) as RelayMessageRow[];
      return rows.map((row) => normalizeRelayMessageRow(row)).filter(isStoredMessage);
    });
  }

  async listPendingMessages(
    daemonId: string,
    options: PendingRelayMessagesOptions = {},
  ): Promise<StoredRelayMessage[]> {
    return this.operation("listPendingMessages", () => {
      const clauses = [
        "daemon_id=?",
        "acked_at=''",
        "delivery_state IN ('pending', 'delivered')",
      ];
      const params: unknown[] = [cleanDaemonId(daemonId)];
      if (options.direction) {
        clauses.push("direction=?");
        params.push(options.direction);
      }
      params.push(clampRelayLimit(options.limit));
      const rows = this.db.prepare(
        `SELECT ${RELAY_MESSAGE_SELECT}
         FROM relay_messages
         WHERE ${clauses.join(" AND ")}
         ORDER BY created_at ASC, id ASC
         LIMIT ?`,
      ).all(...params) as RelayMessageRow[];
      return rows.map((row) => normalizeRelayMessageRow(row)).filter(isStoredMessage);
    });
  }

  async markDeliveryAttempt(messageId: string, epoch: number, deliveredAt = nowIso()): Promise<StoredRelayMessage | null> {
    return this.operation("markDeliveryAttempt", () => {
      this.db.prepare(
        `UPDATE relay_messages
         SET delivery_state='delivered', delivered_at=?, failed_at='',
             delivery_attempts=delivery_attempts + 1,
             last_delivery_error='', last_delivery_epoch=?
         WHERE id=?`,
      ).run(deliveredAt, Math.max(0, Math.floor(Number(epoch || 0))), cleanMessageId(messageId));
      return this.getMessageSync(messageId);
    });
  }

  async markDelivered(messageId: string, deliveredAt = nowIso()): Promise<StoredRelayMessage | null> {
    return this.operation("markDelivered", () => {
      this.db.prepare(
        `UPDATE relay_messages
         SET delivery_state='delivered', delivered_at=?, failed_at='', last_delivery_error=''
         WHERE id=?`,
      ).run(deliveredAt, cleanMessageId(messageId));
      return this.getMessageSync(messageId);
    });
  }

  async markAcked(messageId: string, ackedAt = nowIso()): Promise<StoredRelayMessage | null> {
    return this.operation("markAcked", () => {
      this.db.prepare(
        `UPDATE relay_messages
         SET delivery_state='acked', acked_at=?, failed_at='', last_delivery_error=''
         WHERE id=?`,
      ).run(ackedAt, cleanMessageId(messageId));
      return this.getMessageSync(messageId);
    });
  }

  async markFailed(messageId: string, reason: string, failedAt = nowIso()): Promise<StoredRelayMessage | null> {
    return this.operation("markFailed", () => {
      this.db.prepare(
        `UPDATE relay_messages
         SET delivery_state='failed', failed_at=?, last_delivery_error=?
         WHERE id=?`,
      ).run(failedAt, String(reason || ""), cleanMessageId(messageId));
      return this.getMessageSync(messageId);
    });
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    this.db.close();
  }

  private getInstanceSync(id: string): RelayInstanceRecord | null {
    const row = this.db.prepare(
      `SELECT id, owner_user_id, label, created_at, last_seen_at, metadata_json
       FROM relay_instances WHERE id=?`,
    ).get(String(id || "").trim()) as RelayInstanceRow | undefined;
    return normalizeRelayInstanceRow(row);
  }

  private getMessageSync(messageId: string, options: { idempotent?: boolean } = {}): StoredRelayMessage | null {
    const row = this.db.prepare(
      `SELECT ${RELAY_MESSAGE_SELECT}
       FROM relay_messages WHERE id=?`,
    ).get(cleanMessageId(messageId)) as RelayMessageRow | undefined;
    return normalizeRelayMessageRow(row, { idempotent: Boolean(options.idempotent) });
  }

  private lastChanges(): number {
    const row = this.db.prepare("SELECT changes() AS changes").get() as { changes?: number } | undefined;
    return Number(row?.changes || 0);
  }

  private async operation<T>(name: string, fn: () => T): Promise<T> {
    if (this.closed) throw new RelayStoreError("SQLite relay store is closed", "relay_store_closed");
    try {
      return fn();
    } catch (error) {
      if (error instanceof RelayConflictError || error instanceof RelayStoreError) throw error;
      throw new RelayStoreError(`SQLite RelayStore ${name} failed: ${errorMessage(error)}`, "sqlite_store_error", false, error);
    }
  }
}

function isStoredMessage(value: StoredRelayMessage | null): value is StoredRelayMessage {
  return Boolean(value);
}

function cleanDaemonId(value: string): string {
  return assertNonEmpty(value, "daemon_id");
}

function cleanMessageId(value: string): string {
  return assertNonEmpty(value, "message_id");
}

function assertNonEmpty(value: string, name: string): string {
  const text = String(value || "").trim();
  if (!text) throw new RelayStoreError(`${name} is required`, "relay_store_validation_error");
  return text;
}

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

export class D1RelayStore implements RelayStore {
  readonly kind = "d1";

  constructor(private readonly db: D1Database) {}

  async migrate(): Promise<void> {
    return this.operation("migrate", async () => {
      for (const statement of RELAY_SCHEMA_STATEMENTS) {
        await this.db.prepare(statement).run();
      }
    });
  }

  async upsertInstance(record: RelayInstanceRecord): Promise<RelayInstanceRecord> {
    return this.operation("upsertInstance", async () => {
      assertNonEmpty(record.id, "instance id");
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
      if (!saved) throw new RelayStoreError(`failed to save relay instance ${record.id}`);
      return saved;
    });
  }

  async getInstance(id: string): Promise<RelayInstanceRecord | null> {
    return this.operation("getInstance", async () => {
      const row = await this.db.prepare(
        `SELECT id, owner_user_id, label, created_at, last_seen_at, metadata_json
         FROM relay_instances WHERE id=?`,
      ).bind(String(id || "").trim()).first<RelayInstanceRow>();
      return normalizeRelayInstanceRow(row);
    });
  }

  async appendMessage(envelope: RelayEnvelope, direction: RelayDirection): Promise<StoredRelayMessage> {
    return (await this.appendMessageResult(envelope, direction)).message;
  }

  async appendMessageResult(envelope: RelayEnvelope, direction: RelayDirection): Promise<AppendMessageResult> {
    return this.operation("appendMessage", async () => {
      const expectedHash = relayEnvelopeHash(envelope);
      const result = await this.db.prepare(
        `INSERT OR IGNORE INTO relay_messages
          (${RELAY_MESSAGE_COLUMNS.join(", ")})
         VALUES (${RELAY_MESSAGE_COLUMNS.map(() => "?").join(", ")})`,
      ).bind(...relayMessageValues(envelope, direction)).run();
      const inserted = Number((result as { meta?: { changes?: number } }).meta?.changes || 0) > 0;
      const saved = await this.getMessageInternal(envelope.id, { idempotent: !inserted });
      if (!saved) throw new RelayStoreError(`failed to save relay message ${envelope.id}`);
      if (saved.envelope_hash !== expectedHash || saved.direction !== direction) {
        throw new RelayConflictError(`relay message id conflict for ${envelope.id}`);
      }
      return { message: saved, inserted, idempotent: !inserted };
    });
  }

  async getMessage(messageId: string): Promise<StoredRelayMessage | null> {
    return this.operation("getMessage", async () => this.getMessageInternal(messageId));
  }

  async listMessages(
    daemonId: string,
    options: ListRelayMessagesOptions = {},
  ): Promise<StoredRelayMessage[]> {
    return this.operation("listMessages", async () => {
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
      const result = await this.db.prepare(
        `SELECT ${RELAY_MESSAGE_SELECT}
         FROM relay_messages
         WHERE ${clauses.join(" AND ")}
         ORDER BY created_at ASC, id ASC
         LIMIT ?`,
      ).bind(...params).all<RelayMessageRow>();
      const rows = Array.isArray(result.results) ? result.results : [];
      return rows.map((row) => normalizeRelayMessageRow(row)).filter(isStoredMessage);
    });
  }

  async listPendingMessages(
    daemonId: string,
    options: PendingRelayMessagesOptions = {},
  ): Promise<StoredRelayMessage[]> {
    return this.operation("listPendingMessages", async () => {
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
      const result = await this.db.prepare(
        `SELECT ${RELAY_MESSAGE_SELECT}
         FROM relay_messages
         WHERE ${clauses.join(" AND ")}
         ORDER BY created_at ASC, id ASC
         LIMIT ?`,
      ).bind(...params).all<RelayMessageRow>();
      const rows = Array.isArray(result.results) ? result.results : [];
      return rows.map((row) => normalizeRelayMessageRow(row)).filter(isStoredMessage);
    });
  }

  async markDeliveryAttempt(messageId: string, epoch: number, deliveredAt = nowIso()): Promise<StoredRelayMessage | null> {
    return this.operation("markDeliveryAttempt", async () => {
      await this.db.prepare(
        `UPDATE relay_messages
         SET delivery_state='delivered', delivered_at=?, failed_at='',
             delivery_attempts=delivery_attempts + 1,
             last_delivery_error='', last_delivery_epoch=?
         WHERE id=?`,
      ).bind(deliveredAt, Math.max(0, Math.floor(Number(epoch || 0))), cleanMessageId(messageId)).run();
      return this.getMessageInternal(messageId);
    });
  }

  async markDelivered(messageId: string, deliveredAt = nowIso()): Promise<StoredRelayMessage | null> {
    return this.operation("markDelivered", async () => {
      await this.db.prepare(
        `UPDATE relay_messages
         SET delivery_state='delivered', delivered_at=?, failed_at='', last_delivery_error=''
         WHERE id=?`,
      ).bind(deliveredAt, cleanMessageId(messageId)).run();
      return this.getMessageInternal(messageId);
    });
  }

  async markAcked(messageId: string, ackedAt = nowIso()): Promise<StoredRelayMessage | null> {
    return this.operation("markAcked", async () => {
      await this.db.prepare(
        `UPDATE relay_messages
         SET delivery_state='acked', acked_at=?, failed_at='', last_delivery_error=''
         WHERE id=?`,
      ).bind(ackedAt, cleanMessageId(messageId)).run();
      return this.getMessageInternal(messageId);
    });
  }

  async markFailed(messageId: string, reason: string, failedAt = nowIso()): Promise<StoredRelayMessage | null> {
    return this.operation("markFailed", async () => {
      await this.db.prepare(
        `UPDATE relay_messages
         SET delivery_state='failed', failed_at=?, last_delivery_error=?
         WHERE id=?`,
      ).bind(failedAt, String(reason || ""), cleanMessageId(messageId)).run();
      return this.getMessageInternal(messageId);
    });
  }

  private async getMessageInternal(
    messageId: string,
    options: { idempotent?: boolean } = {},
  ): Promise<StoredRelayMessage | null> {
    const row = await this.db.prepare(
      `SELECT ${RELAY_MESSAGE_SELECT}
       FROM relay_messages WHERE id=?`,
    ).bind(cleanMessageId(messageId)).first<RelayMessageRow>();
    return normalizeRelayMessageRow(row, { idempotent: Boolean(options.idempotent) });
  }

  private async operation<T>(name: string, fn: () => Promise<T>): Promise<T> {
    try {
      return await fn();
    } catch (error) {
      if (error instanceof RelayConflictError || error instanceof RelayStoreError) throw error;
      throw new RelayStoreError(`D1 RelayStore ${name} failed: ${errorMessage(error)}`, "d1_store_error", false, error);
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

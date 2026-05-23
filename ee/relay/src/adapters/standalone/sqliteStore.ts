// node:sqlite ships with modern Node. The @types surface can lag current Node,
// so keep this import localized to the standalone adapter.
// @ts-ignore -- node:sqlite exists at runtime on Node >=22.5.
import { DatabaseSync } from "node:sqlite";

import type { RelayEnvelope } from "../../core/protocol.js";
import { RelayConflictError, RelayStoreError, errorMessage } from "../../core/errors.js";
import type {
  AppendMessageResult,
  ClaimInstanceOwnerArgs,
  ClaimInstanceOwnerResult,
  ListRelayMessagesOptions,
  PendingRelayMessagesOptions,
  RelayClientSessionRecord,
  RelayDaemonCredentialRecord,
  RelayDirection,
  RelayInstanceRecord,
  RelayPairingTokenRecord,
  RelayStore,
  StoredRelayMessage,
} from "../../core/ports.js";
import {
  RELAY_INSTANCE_COORDINATION_COLUMNS,
  RELAY_INSTANCE_SELECT,
  RELAY_MESSAGE_COLUMNS,
  RELAY_MESSAGE_SELECT,
  RELAY_SCHEMA_VERSION,
  RELAY_SCHEMA_STATEMENTS,
  clampRelayLimit,
  normalizeRelayClientSessionRow,
  normalizeRelayDaemonCredentialRow,
  normalizeRelayInstanceRow,
  normalizeRelayMessageRow,
  normalizeRelayPairingTokenRow,
  nowIso,
  relayClientSessionValues,
  relayDaemonCredentialValues,
  relayEnvelopeHash,
  relayInstanceValues,
  relayMessageValues,
  relayPairingTokenValues,
  type RelayClientSessionRow,
  type RelayDaemonCredentialRow,
  type RelayInstanceRow,
  type RelayMessageRow,
  type RelayPairingTokenRow,
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
    this.db.exec("PRAGMA busy_timeout=5000");
  }

  async migrate(): Promise<void> {
    return this.operation("migrate", () => {
      for (const statement of RELAY_SCHEMA_STATEMENTS) {
        this.db.exec(statement);
      }
      this.ensureRelayInstanceCoordinationColumnsSync();
    });
  }

  async upsertInstance(record: RelayInstanceRecord): Promise<RelayInstanceRecord> {
    return this.operation("upsertInstance", () => {
      assertNonEmpty(record.id, "instance id");
      this.db.prepare(
        `INSERT INTO relay_instances
          (id, owner_user_id, label, created_at, last_seen_at, fencing_epoch, active_credential_id, coordination_updated_at, metadata_json)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(id) DO UPDATE SET
          owner_user_id=excluded.owner_user_id,
          label=excluded.label,
          last_seen_at=excluded.last_seen_at,
          fencing_epoch=MAX(relay_instances.fencing_epoch, excluded.fencing_epoch),
          active_credential_id=excluded.active_credential_id,
          coordination_updated_at=excluded.coordination_updated_at,
          metadata_json=excluded.metadata_json`,
      ).run(...relayInstanceValues(record));
      const saved = this.getInstanceSync(record.id);
      if (!saved) throw new RelayStoreError(`failed to save relay instance ${record.id}`);
      return saved;
    });
  }

  async claimInstanceOwner(args: ClaimInstanceOwnerArgs): Promise<ClaimInstanceOwnerResult> {
    return this.operation("claimInstanceOwner", () => {
      const id = assertNonEmpty(args.id, "instance id");
      const ownerUserId = assertNonEmpty(args.ownerUserId, "owner_user_id");
      const fencingEpoch = Math.max(0, Math.floor(Number(args.fencingEpoch || 0)));
      const now = String(args.now || nowIso());
      let saved: RelayInstanceRecord | null = null;

      this.db.exec("BEGIN IMMEDIATE");
      try {
        const existing = this.getInstanceSync(id);
        if (existing?.owner_user_id && existing.owner_user_id !== ownerUserId) {
          this.db.exec("ROLLBACK");
          return { claimed: false, record: existing, reason: "daemon_owner_mismatch" };
        }
        if (existing && Number(existing.fencing_epoch || 0) > fencingEpoch) {
          this.db.exec("ROLLBACK");
          return { claimed: false, record: existing, reason: "stale_fencing_epoch" };
        }

        const record: RelayInstanceRecord = {
          id,
          owner_user_id: ownerUserId,
          label: String(args.label || existing?.label || id),
          created_at: existing?.created_at || now,
          last_seen_at: now,
          fencing_epoch: fencingEpoch,
          active_credential_id: String(args.credentialId || existing?.active_credential_id || ""),
          coordination_updated_at: now,
          metadata: args.metadata || existing?.metadata || {},
        };

        if (existing) {
          this.db.prepare(
            `UPDATE relay_instances
             SET owner_user_id=?, label=?, last_seen_at=?, fencing_epoch=?,
                 active_credential_id=?, coordination_updated_at=?, metadata_json=?
             WHERE id=? AND (owner_user_id='' OR owner_user_id=?) AND fencing_epoch<=?`,
          ).run(
            record.owner_user_id,
            record.label,
            record.last_seen_at,
            record.fencing_epoch,
            record.active_credential_id,
            record.coordination_updated_at,
            relayInstanceValues(record)[8],
            id,
            ownerUserId,
            fencingEpoch,
          );
        } else {
          this.db.prepare(
            `INSERT INTO relay_instances
              (id, owner_user_id, label, created_at, last_seen_at, fencing_epoch, active_credential_id, coordination_updated_at, metadata_json)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          ).run(...relayInstanceValues(record));
        }

        saved = this.getInstanceSync(id);
        this.db.exec("COMMIT");
      } catch (error) {
        try {
          this.db.exec("ROLLBACK");
        } catch {
          // Ignore rollback errors; the original error is more useful.
        }
        throw error;
      }

      if (!saved) return { claimed: false, reason: "relay_instance_claim_failed" };
      if (saved.owner_user_id !== ownerUserId) return { claimed: false, record: saved, reason: "daemon_owner_mismatch" };
      if (Number(saved.fencing_epoch || 0) > fencingEpoch) {
        return { claimed: false, record: saved, reason: "stale_fencing_epoch" };
      }
      return { claimed: true, record: saved };
    });
  }

  async getInstance(id: string): Promise<RelayInstanceRecord | null> {
    return this.operation("getInstance", () => this.getInstanceSync(id));
  }

  async createPairingToken(record: RelayPairingTokenRecord): Promise<RelayPairingTokenRecord> {
    return this.operation("createPairingToken", () => {
      assertNonEmpty(record.id, "pairing token id");
      assertNonEmpty(record.token_hash, "pairing token hash");
      this.db.prepare(
        `INSERT INTO relay_pairing_tokens
          (id, token_hash, owner_user_id, daemon_id, label, created_at, expires_at, consumed_at, revoked_at, metadata_json)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).run(...relayPairingTokenValues(record));
      const saved = this.getPairingTokenByHashSync(record.token_hash);
      if (!saved) throw new RelayStoreError(`failed to save pairing token ${record.id}`);
      return saved;
    });
  }

  async consumePairingToken(tokenHash: string, consumedAt = nowIso()): Promise<RelayPairingTokenRecord | null> {
    return this.operation("consumePairingToken", () => {
      const hash = assertNonEmpty(tokenHash, "pairing token hash");
      this.db.prepare(
        `UPDATE relay_pairing_tokens
         SET consumed_at=?
         WHERE token_hash=? AND consumed_at='' AND revoked_at='' AND expires_at>?`,
      ).run(consumedAt, hash, consumedAt);
      if (this.lastChanges() === 0) return null;
      return this.getPairingTokenByHashSync(hash);
    });
  }

  async createDaemonCredential(record: RelayDaemonCredentialRecord): Promise<RelayDaemonCredentialRecord> {
    return this.operation("createDaemonCredential", () => {
      assertNonEmpty(record.credential_id, "credential id");
      assertNonEmpty(record.daemon_id, "credential daemon_id");
      assertNonEmpty(record.owner_user_id, "credential owner_user_id");
      this.db.prepare(
        `INSERT INTO relay_daemon_credentials
          (credential_id, daemon_id, owner_user_id, public_key_jwk_json, alg, created_at, last_used_at, revoked_at, metadata_json)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).run(...relayDaemonCredentialValues(record));
      const saved = this.getDaemonCredentialSync(record.daemon_id, record.credential_id);
      if (!saved) throw new RelayStoreError(`failed to save daemon credential ${record.credential_id}`);
      return saved;
    });
  }

  async getDaemonCredential(daemonId: string, credentialId: string): Promise<RelayDaemonCredentialRecord | null> {
    return this.operation("getDaemonCredential", () => this.getDaemonCredentialSync(daemonId, credentialId));
  }

  async touchDaemonCredential(credentialId: string, lastUsedAt = nowIso()): Promise<RelayDaemonCredentialRecord | null> {
    return this.operation("touchDaemonCredential", () => {
      const id = assertNonEmpty(credentialId, "credential id");
      this.db.prepare(
        `UPDATE relay_daemon_credentials SET last_used_at=? WHERE credential_id=?`,
      ).run(lastUsedAt, id);
      return this.getDaemonCredentialByIdSync(id);
    });
  }

  async revokeDaemonCredential(credentialId: string, revokedAt = nowIso()): Promise<RelayDaemonCredentialRecord | null> {
    return this.operation("revokeDaemonCredential", () => {
      const id = assertNonEmpty(credentialId, "credential id");
      this.db.prepare(
        `UPDATE relay_daemon_credentials SET revoked_at=? WHERE credential_id=?`,
      ).run(revokedAt, id);
      return this.getDaemonCredentialByIdSync(id);
    });
  }

  async recordAuthNonce(credentialId: string, nonceHash: string, expiresAt: string, createdAt = nowIso()): Promise<boolean> {
    return this.operation("recordAuthNonce", () => {
      this.pruneExpiredAuthNoncesSync(createdAt);
      this.db.prepare(
        `INSERT OR IGNORE INTO relay_auth_nonces
          (credential_id, nonce_hash, created_at, expires_at)
         VALUES (?, ?, ?, ?)`,
      ).run(
        assertNonEmpty(credentialId, "credential id"),
        assertNonEmpty(nonceHash, "nonce hash"),
        createdAt,
        assertNonEmpty(expiresAt, "nonce expires_at"),
      );
      return this.lastChanges() > 0;
    });
  }

  async createClientSession(record: RelayClientSessionRecord): Promise<RelayClientSessionRecord> {
    return this.operation("createClientSession", () => {
      assertNonEmpty(record.session_id, "session id");
      assertNonEmpty(record.token_hash, "session token hash");
      assertNonEmpty(record.owner_user_id, "session owner_user_id");
      this.db.prepare(
        `INSERT INTO relay_client_sessions
          (session_id, token_hash, owner_user_id, created_at, expires_at, revoked_at, metadata_json)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      ).run(...relayClientSessionValues(record));
      const saved = this.getClientSessionByTokenHashSync(record.token_hash);
      if (!saved) throw new RelayStoreError(`failed to save client session ${record.session_id}`);
      return saved;
    });
  }


  async getClientSession(sessionId: string): Promise<RelayClientSessionRecord | null> {
    return this.operation("getClientSession", () => this.getClientSessionByIdSync(sessionId));
  }

  async getClientSessionByTokenHash(tokenHash: string): Promise<RelayClientSessionRecord | null> {
    return this.operation("getClientSessionByTokenHash", () => this.getClientSessionByTokenHashSync(tokenHash));
  }

  async revokeClientSession(sessionId: string, revokedAt = nowIso()): Promise<RelayClientSessionRecord | null> {
    return this.operation("revokeClientSession", () => {
      const id = assertNonEmpty(sessionId, "session id");
      this.db.prepare(
        `UPDATE relay_client_sessions SET revoked_at=? WHERE session_id=?`,
      ).run(revokedAt, id);
      const row = this.db.prepare(
        `SELECT session_id, token_hash, owner_user_id, created_at, expires_at, revoked_at, metadata_json
         FROM relay_client_sessions WHERE session_id=?`,
      ).get(id) as RelayClientSessionRow | undefined;
      return normalizeRelayClientSessionRow(row);
    });
  }

  async pruneExpiredAuthNonces(now = nowIso()): Promise<void> {
    return this.operation("pruneExpiredAuthNonces", () => this.pruneExpiredAuthNoncesSync(now));
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
      `SELECT ${RELAY_INSTANCE_SELECT}
       FROM relay_instances WHERE id=?`,
    ).get(String(id || "").trim()) as RelayInstanceRow | undefined;
    return normalizeRelayInstanceRow(row);
  }

  private ensureRelayInstanceCoordinationColumnsSync(): void {
    const rows = this.db.prepare("PRAGMA table_info(relay_instances)").all() as Array<{ name?: string }>;
    const names = new Set(rows.map((row) => String(row.name || "")));
    for (const column of RELAY_INSTANCE_COORDINATION_COLUMNS) {
      if (names.has(column.name)) continue;
      this.db.exec(`ALTER TABLE relay_instances ADD COLUMN ${column.name} ${column.definition}`);
    }
    this.db.prepare("INSERT OR REPLACE INTO relay_meta (key, value) VALUES ('schema_version', ?)").run(String(RELAY_SCHEMA_VERSION));
  }

  private getPairingTokenByHashSync(tokenHash: string): RelayPairingTokenRecord | null {
    const row = this.db.prepare(
      `SELECT id, token_hash, owner_user_id, daemon_id, label, created_at, expires_at, consumed_at, revoked_at, metadata_json
       FROM relay_pairing_tokens WHERE token_hash=?`,
    ).get(assertNonEmpty(tokenHash, "pairing token hash")) as RelayPairingTokenRow | undefined;
    return normalizeRelayPairingTokenRow(row);
  }

  private getDaemonCredentialSync(daemonId: string, credentialId: string): RelayDaemonCredentialRecord | null {
    const row = this.db.prepare(
      `SELECT credential_id, daemon_id, owner_user_id, public_key_jwk_json, alg, created_at, last_used_at, revoked_at, metadata_json
       FROM relay_daemon_credentials WHERE daemon_id=? AND credential_id=?`,
    ).get(cleanDaemonId(daemonId), assertNonEmpty(credentialId, "credential id")) as RelayDaemonCredentialRow | undefined;
    return normalizeRelayDaemonCredentialRow(row);
  }

  private getDaemonCredentialByIdSync(credentialId: string): RelayDaemonCredentialRecord | null {
    const row = this.db.prepare(
      `SELECT credential_id, daemon_id, owner_user_id, public_key_jwk_json, alg, created_at, last_used_at, revoked_at, metadata_json
       FROM relay_daemon_credentials WHERE credential_id=?`,
    ).get(assertNonEmpty(credentialId, "credential id")) as RelayDaemonCredentialRow | undefined;
    return normalizeRelayDaemonCredentialRow(row);
  }

  private getClientSessionByIdSync(sessionId: string): RelayClientSessionRecord | null {
    const row = this.db.prepare(
      `SELECT session_id, token_hash, owner_user_id, created_at, expires_at, revoked_at, metadata_json
       FROM relay_client_sessions WHERE session_id=?`,
    ).get(assertNonEmpty(sessionId, "session id")) as RelayClientSessionRow | undefined;
    return normalizeRelayClientSessionRow(row);
  }

  private getClientSessionByTokenHashSync(tokenHash: string): RelayClientSessionRecord | null {
    const row = this.db.prepare(
      `SELECT session_id, token_hash, owner_user_id, created_at, expires_at, revoked_at, metadata_json
       FROM relay_client_sessions WHERE token_hash=?`,
    ).get(assertNonEmpty(tokenHash, "session token hash")) as RelayClientSessionRow | undefined;
    return normalizeRelayClientSessionRow(row);
  }

  private pruneExpiredAuthNoncesSync(now: string): void {
    this.db.prepare(`DELETE FROM relay_auth_nonces WHERE expires_at<=?`).run(now);
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

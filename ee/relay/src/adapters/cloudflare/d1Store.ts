import type { RelayEnvelope } from "../../core/protocol.js";
import { RelayConflictError, RelayStoreError, errorMessage } from "../../core/errors.js";
import type {
  AppendMessageResult,
  ClaimInstanceOwnerArgs,
  ClaimInstanceOwnerResult,
  ListRelayMessagesOptions,
  PendingRelayMessagesOptions,
  RelayClientEstablishCodeRecord,
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
  normalizeRelayClientEstablishCodeRow,
  normalizeRelayClientSessionRow,
  normalizeRelayDaemonCredentialRow,
  normalizeRelayInstanceRow,
  normalizeRelayMessageRow,
  normalizeRelayPairingTokenRow,
  nowIso,
  relayClientEstablishCodeValues,
  relayClientSessionValues,
  relayDaemonCredentialValues,
  relayEnvelopeHash,
  relayInstanceValues,
  relayMessageValues,
  relayPairingTokenValues,
  type RelayClientEstablishCodeRow,
  type RelayClientSessionRow,
  type RelayDaemonCredentialRow,
  type RelayInstanceRow,
  type RelayMessageRow,
  type RelayPairingTokenRow,
} from "../../core/sql.js";

export class D1RelayStore implements RelayStore {
  readonly kind = "d1";

  constructor(private readonly db: D1Database) {}

  async migrate(): Promise<void> {
    return this.operation("migrate", async () => {
      for (const statement of RELAY_SCHEMA_STATEMENTS) {
        await this.db.prepare(statement).run();
      }
      await this.ensureRelayInstanceCoordinationColumns();
    });
  }

  async upsertInstance(record: RelayInstanceRecord): Promise<RelayInstanceRecord> {
    return this.operation("upsertInstance", async () => {
      assertNonEmpty(record.id, "instance id");
      await this.db.prepare(
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
      ).bind(...relayInstanceValues(record)).run();
      const saved = await this.getInstance(record.id);
      if (!saved) throw new RelayStoreError(`failed to save relay instance ${record.id}`);
      return saved;
    });
  }

  async claimInstanceOwner(args: ClaimInstanceOwnerArgs): Promise<ClaimInstanceOwnerResult> {
    return this.operation("claimInstanceOwner", async () => {
      const id = assertNonEmpty(args.id, "instance id");
      const ownerUserId = assertNonEmpty(args.ownerUserId, "owner_user_id");
      const fencingEpoch = Math.max(0, Math.floor(Number(args.fencingEpoch || 0)));
      const now = String(args.now || nowIso());
      const existing = await this.getInstance(id);
      if (existing?.owner_user_id && existing.owner_user_id !== ownerUserId) {
        return { claimed: false, record: existing, reason: "daemon_owner_mismatch" };
      }
      if (existing && Number(existing.fencing_epoch || 0) > fencingEpoch) {
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
        await this.db.prepare(
          `UPDATE relay_instances
           SET owner_user_id=?, label=?, last_seen_at=?, fencing_epoch=?,
               active_credential_id=?, coordination_updated_at=?, metadata_json=?
           WHERE id=? AND (owner_user_id='' OR owner_user_id=?) AND fencing_epoch<=?`,
        ).bind(
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
        ).run();
      } else {
        await this.db.prepare(
          `INSERT OR IGNORE INTO relay_instances
            (id, owner_user_id, label, created_at, last_seen_at, fencing_epoch, active_credential_id, coordination_updated_at, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        ).bind(...relayInstanceValues(record)).run();
      }

      const saved = await this.getInstance(id);
      if (!saved) return { claimed: false, reason: "relay_instance_claim_failed" };
      if (saved.owner_user_id !== ownerUserId) return { claimed: false, record: saved, reason: "daemon_owner_mismatch" };
      if (Number(saved.fencing_epoch || 0) > fencingEpoch) {
        return { claimed: false, record: saved, reason: "stale_fencing_epoch" };
      }
      return { claimed: true, record: saved };
    });
  }

  async getInstance(id: string): Promise<RelayInstanceRecord | null> {
    return this.operation("getInstance", async () => {
      const row = await this.db.prepare(
        `SELECT ${RELAY_INSTANCE_SELECT}
         FROM relay_instances WHERE id=?`,
      ).bind(String(id || "").trim()).first<RelayInstanceRow>();
      return normalizeRelayInstanceRow(row);
    });
  }

  async createPairingToken(record: RelayPairingTokenRecord): Promise<RelayPairingTokenRecord> {
    return this.operation("createPairingToken", async () => {
      assertNonEmpty(record.id, "pairing token id");
      assertNonEmpty(record.token_hash, "pairing token hash");
      await this.db.prepare(
        `INSERT INTO relay_pairing_tokens
          (id, token_hash, owner_user_id, daemon_id, label, created_at, expires_at, consumed_at, revoked_at, metadata_json)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(...relayPairingTokenValues(record)).run();
      const saved = await this.getPairingTokenByHash(record.token_hash);
      if (!saved) throw new RelayStoreError(`failed to save pairing token ${record.id}`);
      return saved;
    });
  }

  async consumePairingToken(tokenHash: string, consumedAt = nowIso()): Promise<RelayPairingTokenRecord | null> {
    return this.operation("consumePairingToken", async () => {
      const hash = assertNonEmpty(tokenHash, "pairing token hash");
      const result = await this.db.prepare(
        `UPDATE relay_pairing_tokens
         SET consumed_at=?
         WHERE token_hash=? AND consumed_at='' AND revoked_at='' AND expires_at>?`,
      ).bind(consumedAt, hash, consumedAt).run();
      if (changesFromResult(result) === 0) return null;
      return this.getPairingTokenByHash(hash);
    });
  }

  async createClientEstablishCode(record: RelayClientEstablishCodeRecord): Promise<RelayClientEstablishCodeRecord> {
    return this.operation("createClientEstablishCode", async () => {
      assertNonEmpty(record.id, "establish code id");
      assertNonEmpty(record.code_hash, "establish code hash");
      assertNonEmpty(record.owner_user_id, "establish code owner_user_id");
      await this.db.prepare(
        `INSERT INTO relay_client_establish_codes
          (id, code_hash, owner_user_id, daemon_id, label, created_at, expires_at, consumed_at, revoked_at, metadata_json)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(...relayClientEstablishCodeValues(record)).run();
      const saved = await this.getClientEstablishCodeByHash(record.code_hash);
      if (!saved) throw new RelayStoreError(`failed to save establish code ${record.id}`);
      return saved;
    });
  }

  async consumeClientEstablishCode(codeHash: string, consumedAt = nowIso()): Promise<RelayClientEstablishCodeRecord | null> {
    return this.operation("consumeClientEstablishCode", async () => {
      const hash = assertNonEmpty(codeHash, "establish code hash");
      // Atomic single-use: the conditional UPDATE only matches an unconsumed,
      // unrevoked, unexpired code. changes==0 means it was already redeemed (or
      // never valid), so two concurrent /establish calls cannot both mint.
      const result = await this.db.prepare(
        `UPDATE relay_client_establish_codes
         SET consumed_at=?
         WHERE code_hash=? AND consumed_at='' AND revoked_at='' AND expires_at>?`,
      ).bind(consumedAt, hash, consumedAt).run();
      if (changesFromResult(result) === 0) return null;
      return this.getClientEstablishCodeByHash(hash);
    });
  }

  async createDaemonCredential(record: RelayDaemonCredentialRecord): Promise<RelayDaemonCredentialRecord> {
    return this.operation("createDaemonCredential", async () => {
      assertNonEmpty(record.credential_id, "credential id");
      assertNonEmpty(record.daemon_id, "credential daemon_id");
      assertNonEmpty(record.owner_user_id, "credential owner_user_id");
      await this.db.prepare(
        `INSERT INTO relay_daemon_credentials
          (credential_id, daemon_id, owner_user_id, public_key_jwk_json, alg, created_at, last_used_at, revoked_at, metadata_json)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(...relayDaemonCredentialValues(record)).run();
      const saved = await this.getDaemonCredential(record.daemon_id, record.credential_id);
      if (!saved) throw new RelayStoreError(`failed to save daemon credential ${record.credential_id}`);
      return saved;
    });
  }

  async getDaemonCredential(daemonId: string, credentialId: string): Promise<RelayDaemonCredentialRecord | null> {
    return this.operation("getDaemonCredential", async () => {
      const row = await this.db.prepare(
        `SELECT credential_id, daemon_id, owner_user_id, public_key_jwk_json, alg, created_at, last_used_at, revoked_at, metadata_json
         FROM relay_daemon_credentials WHERE daemon_id=? AND credential_id=?`,
      ).bind(cleanDaemonId(daemonId), assertNonEmpty(credentialId, "credential id")).first<RelayDaemonCredentialRow>();
      return normalizeRelayDaemonCredentialRow(row);
    });
  }

  async touchDaemonCredential(credentialId: string, lastUsedAt = nowIso()): Promise<RelayDaemonCredentialRecord | null> {
    return this.operation("touchDaemonCredential", async () => {
      const id = assertNonEmpty(credentialId, "credential id");
      await this.db.prepare(
        `UPDATE relay_daemon_credentials SET last_used_at=? WHERE credential_id=?`,
      ).bind(lastUsedAt, id).run();
      return this.getDaemonCredentialById(id);
    });
  }

  async revokeDaemonCredential(credentialId: string, revokedAt = nowIso()): Promise<RelayDaemonCredentialRecord | null> {
    return this.operation("revokeDaemonCredential", async () => {
      const id = assertNonEmpty(credentialId, "credential id");
      await this.db.prepare(
        `UPDATE relay_daemon_credentials SET revoked_at=? WHERE credential_id=?`,
      ).bind(revokedAt, id).run();
      return this.getDaemonCredentialById(id);
    });
  }

  async recordAuthNonce(credentialId: string, nonceHash: string, expiresAt: string, createdAt = nowIso()): Promise<boolean> {
    return this.operation("recordAuthNonce", async () => {
      await this.pruneExpiredAuthNonces(createdAt);
      const result = await this.db.prepare(
        `INSERT OR IGNORE INTO relay_auth_nonces
          (credential_id, nonce_hash, created_at, expires_at)
         VALUES (?, ?, ?, ?)`,
      ).bind(
        assertNonEmpty(credentialId, "credential id"),
        assertNonEmpty(nonceHash, "nonce hash"),
        createdAt,
        assertNonEmpty(expiresAt, "nonce expires_at"),
      ).run();
      return changesFromResult(result) > 0;
    });
  }

  async createClientSession(record: RelayClientSessionRecord): Promise<RelayClientSessionRecord> {
    return this.operation("createClientSession", async () => {
      assertNonEmpty(record.session_id, "session id");
      assertNonEmpty(record.token_hash, "session token hash");
      assertNonEmpty(record.owner_user_id, "session owner_user_id");
      await this.db.prepare(
        `INSERT INTO relay_client_sessions
          (session_id, token_hash, owner_user_id, created_at, expires_at, revoked_at, metadata_json)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      ).bind(...relayClientSessionValues(record)).run();
      const saved = await this.getClientSessionByTokenHash(record.token_hash);
      if (!saved) throw new RelayStoreError(`failed to save client session ${record.session_id}`);
      return saved;
    });
  }


  async getClientSession(sessionId: string): Promise<RelayClientSessionRecord | null> {
    return this.operation("getClientSession", async () => {
      const row = await this.db.prepare(
        `SELECT session_id, token_hash, owner_user_id, created_at, expires_at, revoked_at, metadata_json
         FROM relay_client_sessions WHERE session_id=?`,
      ).bind(assertNonEmpty(sessionId, "session id")).first<RelayClientSessionRow>();
      return normalizeRelayClientSessionRow(row);
    });
  }

  async getClientSessionByTokenHash(tokenHash: string): Promise<RelayClientSessionRecord | null> {
    return this.operation("getClientSessionByTokenHash", async () => {
      const row = await this.db.prepare(
        `SELECT session_id, token_hash, owner_user_id, created_at, expires_at, revoked_at, metadata_json
         FROM relay_client_sessions WHERE token_hash=?`,
      ).bind(assertNonEmpty(tokenHash, "session token hash")).first<RelayClientSessionRow>();
      return normalizeRelayClientSessionRow(row);
    });
  }

  async revokeClientSession(sessionId: string, revokedAt = nowIso()): Promise<RelayClientSessionRecord | null> {
    return this.operation("revokeClientSession", async () => {
      const id = assertNonEmpty(sessionId, "session id");
      await this.db.prepare(
        `UPDATE relay_client_sessions SET revoked_at=? WHERE session_id=?`,
      ).bind(revokedAt, id).run();
      const row = await this.db.prepare(
        `SELECT session_id, token_hash, owner_user_id, created_at, expires_at, revoked_at, metadata_json
         FROM relay_client_sessions WHERE session_id=?`,
      ).bind(id).first<RelayClientSessionRow>();
      return normalizeRelayClientSessionRow(row);
    });
  }

  async pruneExpiredAuthNonces(now = nowIso()): Promise<void> {
    return this.operation("pruneExpiredAuthNonces", async () => {
      await this.db.prepare(`DELETE FROM relay_auth_nonces WHERE expires_at<=?`).bind(now).run();
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
      const inserted = changesFromResult(result) > 0;
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

  private async ensureRelayInstanceCoordinationColumns(): Promise<void> {
    const result = await this.db.prepare("PRAGMA table_info(relay_instances)").all<{ name?: string }>();
    const rows = Array.isArray(result.results) ? result.results : [];
    const names = new Set(rows.map((row) => String(row.name || "")));
    for (const column of RELAY_INSTANCE_COORDINATION_COLUMNS) {
      if (names.has(column.name)) continue;
      await this.db.prepare(`ALTER TABLE relay_instances ADD COLUMN ${column.name} ${column.definition}`).run();
    }
    await this.db.prepare("INSERT OR REPLACE INTO relay_meta (key, value) VALUES ('schema_version', ?)").bind(String(RELAY_SCHEMA_VERSION)).run();
  }

  private async getPairingTokenByHash(tokenHash: string): Promise<RelayPairingTokenRecord | null> {
    const row = await this.db.prepare(
      `SELECT id, token_hash, owner_user_id, daemon_id, label, created_at, expires_at, consumed_at, revoked_at, metadata_json
       FROM relay_pairing_tokens WHERE token_hash=?`,
    ).bind(assertNonEmpty(tokenHash, "pairing token hash")).first<RelayPairingTokenRow>();
    return normalizeRelayPairingTokenRow(row);
  }

  private async getClientEstablishCodeByHash(codeHash: string): Promise<RelayClientEstablishCodeRecord | null> {
    const row = await this.db.prepare(
      `SELECT id, code_hash, owner_user_id, daemon_id, label, created_at, expires_at, consumed_at, revoked_at, metadata_json
       FROM relay_client_establish_codes WHERE code_hash=?`,
    ).bind(assertNonEmpty(codeHash, "establish code hash")).first<RelayClientEstablishCodeRow>();
    return normalizeRelayClientEstablishCodeRow(row);
  }

  private async getDaemonCredentialById(credentialId: string): Promise<RelayDaemonCredentialRecord | null> {
    const row = await this.db.prepare(
      `SELECT credential_id, daemon_id, owner_user_id, public_key_jwk_json, alg, created_at, last_used_at, revoked_at, metadata_json
       FROM relay_daemon_credentials WHERE credential_id=?`,
    ).bind(assertNonEmpty(credentialId, "credential id")).first<RelayDaemonCredentialRow>();
    return normalizeRelayDaemonCredentialRow(row);
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

function changesFromResult(result: unknown): number {
  return Number((result as { meta?: { changes?: number } }).meta?.changes || 0);
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

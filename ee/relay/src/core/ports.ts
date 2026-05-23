import type { JsonObject, RelayEnvelope, RelayMessageKind } from "./protocol.js";

export type RelayDirection =
  | "to_daemon"
  | "from_daemon"
  | "to_client"
  | "channel_ingress"
  | "channel_egress";

export type RelayDeliveryState = "pending" | "delivered" | "acked" | "failed";

export interface RelayInstanceRecord {
  id: string;
  owner_user_id: string;
  label: string;
  created_at: string;
  last_seen_at: string;
  fencing_epoch: number;
  active_credential_id: string;
  coordination_updated_at: string;
  metadata: JsonObject;
}

export interface StoredRelayMessage {
  id: string;
  daemon_id: string;
  direction: RelayDirection;
  kind: RelayMessageKind;
  source: JsonObject;
  target: JsonObject;
  payload: JsonObject;
  envelope: RelayEnvelope;
  envelope_hash: string;
  created_at: string;
  delivery_state: RelayDeliveryState;
  delivered_at: string;
  acked_at: string;
  failed_at: string;
  delivery_attempts: number;
  last_delivery_error: string;
  last_delivery_epoch: number;
  idempotent: boolean;
}

export interface ListRelayMessagesOptions {
  direction?: RelayDirection;
  since_created_at?: string;
  include_acked?: boolean;
  limit?: number;
}

export interface PendingRelayMessagesOptions {
  direction?: RelayDirection;
  limit?: number;
}

export interface AppendMessageResult {
  message: StoredRelayMessage;
  inserted: boolean;
  idempotent: boolean;
}

export interface RelayPairingTokenRecord {
  id: string;
  token_hash: string;
  owner_user_id: string;
  daemon_id: string;
  label: string;
  created_at: string;
  expires_at: string;
  consumed_at: string;
  revoked_at: string;
  metadata: JsonObject;
}

export interface RelayDaemonCredentialRecord {
  credential_id: string;
  daemon_id: string;
  owner_user_id: string;
  public_key_jwk: JsonObject;
  alg: "ES256" | string;
  created_at: string;
  last_used_at: string;
  revoked_at: string;
  metadata: JsonObject;
}

export interface RelayClientSessionRecord {
  session_id: string;
  token_hash: string;
  owner_user_id: string;
  created_at: string;
  expires_at: string;
  revoked_at: string;
  metadata: JsonObject;
}

// Single-use, short-lived, hashed code that the same-origin /establish endpoint
// trades for a freshly minted client session. Parallels relay_pairing_tokens:
// the raw code is handed out once (QR/link); only its hash is stored; consume is
// atomic so the code can be redeemed exactly once.
export interface RelayClientEstablishCodeRecord {
  id: string;
  code_hash: string;
  owner_user_id: string;
  daemon_id: string;
  label: string;
  created_at: string;
  expires_at: string;
  consumed_at: string;
  revoked_at: string;
  metadata: JsonObject;
}

export interface RelayAuthStore {
  createPairingToken(record: RelayPairingTokenRecord): Promise<RelayPairingTokenRecord>;
  consumePairingToken(tokenHash: string, consumedAt?: string): Promise<RelayPairingTokenRecord | null>;
  createClientEstablishCode(record: RelayClientEstablishCodeRecord): Promise<RelayClientEstablishCodeRecord>;
  // Atomic single-use redemption: returns the record only if it was not already
  // consumed/revoked/expired (so two concurrent /establish calls cannot both win).
  consumeClientEstablishCode(codeHash: string, consumedAt?: string): Promise<RelayClientEstablishCodeRecord | null>;
  createDaemonCredential(record: RelayDaemonCredentialRecord): Promise<RelayDaemonCredentialRecord>;
  getDaemonCredential(daemonId: string, credentialId: string): Promise<RelayDaemonCredentialRecord | null>;
  touchDaemonCredential(credentialId: string, lastUsedAt?: string): Promise<RelayDaemonCredentialRecord | null>;
  revokeDaemonCredential(credentialId: string, revokedAt?: string): Promise<RelayDaemonCredentialRecord | null>;
  recordAuthNonce(credentialId: string, nonceHash: string, expiresAt: string, createdAt?: string): Promise<boolean>;
  createClientSession(record: RelayClientSessionRecord): Promise<RelayClientSessionRecord>;
  getClientSession(sessionId: string): Promise<RelayClientSessionRecord | null>;
  getClientSessionByTokenHash(tokenHash: string): Promise<RelayClientSessionRecord | null>;
  revokeClientSession(sessionId: string, revokedAt?: string): Promise<RelayClientSessionRecord | null>;
  pruneExpiredAuthNonces?(now?: string): Promise<void>;
}

export interface ClaimInstanceOwnerArgs {
  id: string;
  ownerUserId: string;
  label?: string;
  credentialId?: string;
  fencingEpoch: number;
  now?: string;
  metadata?: JsonObject;
}

export type ClaimInstanceOwnerReason =
  | "daemon_owner_mismatch"
  | "stale_fencing_epoch"
  | "relay_instance_claim_failed";

export interface ClaimInstanceOwnerResult {
  claimed: boolean;
  record?: RelayInstanceRecord;
  reason?: ClaimInstanceOwnerReason;
}

export interface RelayStore extends RelayAuthStore {
  migrate(): Promise<void>;
  upsertInstance(record: RelayInstanceRecord): Promise<RelayInstanceRecord>;
  claimInstanceOwner(args: ClaimInstanceOwnerArgs): Promise<ClaimInstanceOwnerResult>;
  getInstance(id: string): Promise<RelayInstanceRecord | null>;
  appendMessage(envelope: RelayEnvelope, direction: RelayDirection): Promise<StoredRelayMessage>;
  appendMessageResult(envelope: RelayEnvelope, direction: RelayDirection): Promise<AppendMessageResult>;
  getMessage(messageId: string): Promise<StoredRelayMessage | null>;
  listMessages(daemonId: string, options?: ListRelayMessagesOptions): Promise<StoredRelayMessage[]>;
  listPendingMessages(daemonId: string, options?: PendingRelayMessagesOptions): Promise<StoredRelayMessage[]>;
  markDeliveryAttempt(messageId: string, epoch: number, deliveredAt?: string): Promise<StoredRelayMessage | null>;
  markDelivered(messageId: string, deliveredAt?: string): Promise<StoredRelayMessage | null>;
  markAcked(messageId: string, ackedAt?: string): Promise<StoredRelayMessage | null>;
  markFailed(messageId: string, reason: string, failedAt?: string): Promise<StoredRelayMessage | null>;
  close?(): Promise<void> | void;
}

export interface DaemonAuthPrincipal {
  kind: "daemon";
  daemonId: string;
  ownerUserId: string;
  credentialId: string;
  authMode: "signed-attach-v1" | "local-dev-unauthenticated";
}

export interface ClientAuthPrincipal {
  kind: "client";
  ownerUserId: string;
  sessionId: string;
  userId: string;
  authMode: "session-v1" | "local-dev-unauthenticated";
}

export interface RelaySocket {
  id: string;
  sendEnvelope(envelope: RelayEnvelope): Promise<void> | void;
  close?(code?: number, reason?: string): Promise<void> | void;
}

export interface AttachDaemonArgs {
  daemonId: string;
  socket: RelaySocket;
  resumeToken?: string;
  auth?: DaemonAuthPrincipal;
}

export interface AttachClientArgs {
  daemonId: string;
  clientId: string;
  socket: RelaySocket;
  auth?: ClientAuthPrincipal;
}

export interface AttachResult {
  accepted: boolean;
  connectionId: string;
  epoch: number;
  replacedConnectionId?: string;
  reason?: string;
}

export interface RelayDeliveryResult {
  delivered: boolean;
  connectionId?: string;
  epoch?: number;
  reason?: string;
}

export interface RelayBroadcastResult {
  delivered: number;
  connectionIds: string[];
  epoch?: number;
  reason?: string;
}

export interface RendezvousSnapshot {
  daemon_id: string;
  daemon_online: boolean;
  daemon_connection_id: string;
  epoch: number;
  client_connection_ids: string[];
  owner_user_id?: string;
  daemon_credential_id?: string;
}

export interface RelayCoordinator {
  attachDaemon(args: AttachDaemonArgs): Promise<AttachResult> | AttachResult;
  attachClient(args: AttachClientArgs): Promise<AttachResult> | AttachResult;
  detach(connectionId: string): Promise<void> | void;
  isCurrentDaemonConnection(daemonId: string, connectionId: string, epoch?: number): Promise<boolean> | boolean;
  sendToDaemon(daemonId: string, envelope: RelayEnvelope): Promise<RelayDeliveryResult> | RelayDeliveryResult;
  broadcastToClients(daemonId: string, envelope: RelayEnvelope): Promise<RelayBroadcastResult> | RelayBroadcastResult;
  snapshot(daemonId: string): Promise<RendezvousSnapshot> | RendezvousSnapshot;
}

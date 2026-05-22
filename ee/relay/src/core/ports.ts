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

export interface RelayStore {
  migrate(): Promise<void>;
  upsertInstance(record: RelayInstanceRecord): Promise<RelayInstanceRecord>;
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

export interface RelaySocket {
  id: string;
  sendEnvelope(envelope: RelayEnvelope): Promise<void> | void;
  close?(code?: number, reason?: string): Promise<void> | void;
}

export interface AttachDaemonArgs {
  daemonId: string;
  socket: RelaySocket;
  resumeToken?: string;
}

export interface AttachClientArgs {
  daemonId: string;
  clientId: string;
  socket: RelaySocket;
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

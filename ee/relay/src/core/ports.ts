import type { JsonObject, RelayEnvelope, RelayMessageKind } from "./protocol.js";

export type RelayDirection =
  | "to_daemon"
  | "from_daemon"
  | "to_client"
  | "channel_ingress"
  | "channel_egress";

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
  created_at: string;
  delivered_at: string;
  acked_at: string;
}

export interface ListRelayMessagesOptions {
  direction?: RelayDirection;
  since_created_at?: string;
  limit?: number;
}

export interface RelayStore {
  migrate(): Promise<void>;
  upsertInstance(record: RelayInstanceRecord): Promise<RelayInstanceRecord>;
  getInstance(id: string): Promise<RelayInstanceRecord | null>;
  appendMessage(envelope: RelayEnvelope, direction: RelayDirection): Promise<StoredRelayMessage>;
  listMessages(daemonId: string, options?: ListRelayMessagesOptions): Promise<StoredRelayMessage[]>;
  markDelivered(messageId: string, deliveredAt?: string): Promise<StoredRelayMessage | null>;
  markAcked(messageId: string, ackedAt?: string): Promise<StoredRelayMessage | null>;
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
  sendToDaemon(daemonId: string, envelope: RelayEnvelope): Promise<boolean> | boolean;
  broadcastToClients(daemonId: string, envelope: RelayEnvelope): Promise<number> | number;
  snapshot(daemonId: string): Promise<RendezvousSnapshot> | RendezvousSnapshot;
}

import {
  ackIdFromEnvelope,
  makeErrorEnvelope,
  makeRelayEnvelope,
  type RelayEndpoint,
  type RelayEndpointKind,
  type JsonObject,
  type JsonValue,
  type RelayEnvelope,
} from "./protocol.js";
import type {
  AttachResult,
  ClientAuthPrincipal,
  DaemonAuthPrincipal,
  RelayBroadcastResult,
  RelayCoordinator,
  RelayDeliveryResult,
  RelayDirection,
  RelaySocket,
  RelayStore,
  StoredRelayMessage,
} from "./ports.js";
import { RelayError, errorMessage } from "./errors.js";
import { nowIso } from "./sql.js";

export interface RelayRuntimeOptions {
  replayLimit?: number;
  relayId?: string;
  instanceOwnerUserId?: string;
  instanceLabel?: string;
}

export interface RuntimeAttachResult extends AttachResult {
  replayed: number;
  replay_failed: number;
}

export interface RuntimeIngestResult {
  stored?: StoredRelayMessage;
  direction: RelayDirection;
  inserted: boolean;
  idempotent: boolean;
  delivery?: RelayDeliveryResult | RelayBroadcastResult;
  acked?: StoredRelayMessage | null;
}

export class RelayRuntime {
  readonly replayLimit: number;
  readonly relayId: string;

  constructor(
    readonly store: RelayStore,
    readonly coordinator: RelayCoordinator,
    options: RelayRuntimeOptions = {},
  ) {
    this.replayLimit = Math.max(1, Math.min(1000, Math.floor(Number(options.replayLimit || 100))));
    this.relayId = String(options.relayId || "relay").trim() || "relay";
    this.instanceOwnerUserId = String(options.instanceOwnerUserId || "").trim();
    this.instanceLabel = String(options.instanceLabel || "").trim();
  }

  private readonly instanceOwnerUserId: string;
  private readonly instanceLabel: string;

  async attachDaemon(daemonId: string, socket: RelaySocket, auth?: DaemonAuthPrincipal): Promise<RuntimeAttachResult> {
    const id = cleanRequired(daemonId, "daemonId");
    const attached = await this.coordinator.attachDaemon({ daemonId: id, socket, auth });
    if (!attached.accepted) {
      return { ...attached, replayed: 0, replay_failed: 0 };
    }
    await this.store.upsertInstance({
      id,
      owner_user_id: auth?.ownerUserId || this.instanceOwnerUserId,
      label: this.instanceLabel || id,
      created_at: nowIso(),
      last_seen_at: nowIso(),
      metadata: { relay_runtime: "phase1" },
    });
    const replay = await this.replayToDaemon(id);
    return { ...attached, replayed: replay.replayed, replay_failed: replay.failed };
  }

  async attachClient(daemonId: string, clientId: string, socket: RelaySocket, auth?: ClientAuthPrincipal): Promise<RuntimeAttachResult> {
    const attached = await this.coordinator.attachClient({ daemonId, clientId, socket, auth });
    if (!attached.accepted) {
      return { ...attached, replayed: 0, replay_failed: 0 };
    }
    const replay = await this.replayToClients(daemonId);
    return { ...attached, replayed: replay.replayed, replay_failed: replay.failed };
  }

  async handleFromClient(envelope: RelayEnvelope): Promise<RuntimeIngestResult> {
    if (envelope.kind === "ack") {
      const acked = await this.handleAck(envelope);
      return { direction: "to_daemon", inserted: false, idempotent: false, acked };
    }
    const append = await this.store.appendMessageResult(envelope, "to_daemon");
    if (append.idempotent && append.message.delivery_state === "acked") {
      return {
        stored: append.message,
        direction: "to_daemon",
        inserted: false,
        idempotent: true,
        delivery: {
          delivered: false,
          reason: "already_acked",
          epoch: append.message.last_delivery_epoch,
        },
      };
    }
    const delivery = await this.coordinator.sendToDaemon(envelope.daemon_id, envelope);
    if (delivery.delivered) {
      await this.store.markDeliveryAttempt(envelope.id, delivery.epoch || 0);
    }
    return {
      stored: await this.store.getMessage(envelope.id) || append.message,
      direction: "to_daemon",
      inserted: append.inserted,
      idempotent: append.idempotent,
      delivery,
    };
  }

  async handleFromDaemon(connectionId: string, epoch: number, envelope: RelayEnvelope): Promise<RuntimeIngestResult> {
    if (!await this.coordinator.isCurrentDaemonConnection(envelope.daemon_id, connectionId, epoch)) {
      throw new RelayError("stale daemon connection is fenced", "stale_daemon_connection", false);
    }
    if (envelope.kind === "ack") {
      const acked = await this.handleAck(envelope);
      return { direction: "from_daemon", inserted: false, idempotent: false, acked };
    }
    const append = await this.store.appendMessageResult(envelope, "from_daemon");
    if (append.idempotent && append.message.delivery_state === "acked") {
      return {
        stored: append.message,
        direction: "from_daemon",
        inserted: false,
        idempotent: true,
        delivery: {
          delivered: 0,
          connectionIds: [],
          reason: "already_acked",
          epoch: append.message.last_delivery_epoch || epoch || 0,
        },
      };
    }
    const delivery = await this.coordinator.broadcastToClients(envelope.daemon_id, envelope);
    if (delivery.delivered > 0) {
      await this.store.markDeliveryAttempt(envelope.id, delivery.epoch || epoch || 0);
    }
    return {
      stored: await this.store.getMessage(envelope.id) || append.message,
      direction: "from_daemon",
      inserted: append.inserted,
      idempotent: append.idempotent,
      delivery,
    };
  }

  async replayToDaemon(daemonId: string): Promise<{ replayed: number; failed: number }> {
    const pending = await this.store.listPendingMessages(daemonId, {
      direction: "to_daemon",
      limit: this.replayLimit,
    });
    let replayed = 0;
    let failed = 0;
    for (const row of pending) {
      const delivery = await this.coordinator.sendToDaemon(daemonId, row.envelope);
      if (delivery.delivered) {
        await this.store.markDeliveryAttempt(row.id, delivery.epoch || 0);
        replayed += 1;
      } else {
        failed += 1;
      }
    }
    return { replayed, failed };
  }

  async replayToClients(daemonId: string): Promise<{ replayed: number; failed: number }> {
    const pending = await this.store.listPendingMessages(daemonId, {
      direction: "from_daemon",
      limit: this.replayLimit,
    });
    let replayed = 0;
    let failed = 0;
    for (const row of pending) {
      const delivery = await this.coordinator.broadcastToClients(daemonId, row.envelope);
      if (delivery.delivered > 0) {
        await this.store.markDeliveryAttempt(row.id, delivery.epoch || 0);
        replayed += 1;
      } else {
        failed += 1;
      }
    }
    return { replayed, failed };
  }

  async handleAck(envelope: RelayEnvelope): Promise<StoredRelayMessage | null> {
    const ackId = ackIdFromEnvelope(envelope);
    if (!ackId) return null;
    return this.store.markAcked(ackId, envelope.created_at || nowIso());
  }

  makeReadyEnvelope(
    daemonId: string,
    targetId: string,
    payload: JsonObject,
    targetKind: RelayEndpointKind = "remote-client",
  ): RelayEnvelope {
    return makeRelayEnvelope({
      daemon_id: daemonId || "relay",
      source: { kind: "relay", id: this.relayId },
      target: controlTarget(daemonId, targetId, targetKind),
      kind: "ready",
      payload,
    });
  }

  makeErrorEnvelope(
    daemonId: string,
    targetId: string,
    error: unknown,
    refId = "",
    targetKind: RelayEndpointKind = "remote-client",
  ): RelayEnvelope {
    return makeErrorEnvelope({
      daemon_id: daemonId || "relay",
      source: { kind: "relay", id: this.relayId },
      target: controlTarget(daemonId, targetId || "unknown", targetKind),
      code: error instanceof RelayError ? error.code : "relay_runtime_error",
      message: errorMessage(error),
      retryable: error instanceof RelayError ? error.retryable : false,
      ref_id: refId || undefined,
    });
  }
}

export function toJsonObject(value: Record<string, unknown>): JsonObject {
  const output: JsonObject = {};
  for (const [key, item] of Object.entries(value)) {
    if (item === undefined) continue;
    output[key] = toJsonValue(item);
  }
  return output;
}

export function toJsonValue(value: unknown): JsonValue {
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  if (Array.isArray(value)) return value.map(toJsonValue);
  if (value && typeof value === "object") return toJsonObject(value as Record<string, unknown>);
  return String(value);
}

function cleanRequired(value: string, name: string): string {
  const text = String(value || "").trim();
  if (!text) throw new RelayError(`${name} is required`, "relay_runtime_validation_error");
  return text;
}

function controlTarget(daemonId: string, targetId: string, targetKind: RelayEndpointKind): RelayEndpoint {
  if (targetKind === "daemon") {
    return { kind: "daemon", id: daemonId || targetId };
  }
  return { kind: targetKind, id: targetId };
}

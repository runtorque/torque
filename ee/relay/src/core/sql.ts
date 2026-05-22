import type { JsonObject, RelayEnvelope, RelayMessageKind } from "./protocol.js";
import { endpointToJson, parseRelayEnvelope } from "./protocol.js";
import type { RelayDeliveryState, RelayDirection, RelayInstanceRecord, StoredRelayMessage } from "./ports.js";

export const RELAY_SCHEMA_VERSION = 2;

export const RELAY_SCHEMA_STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS relay_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS relay_instances (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
  )`,
  `CREATE TABLE IF NOT EXISTS relay_messages (
    id TEXT PRIMARY KEY,
    daemon_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_json TEXT NOT NULL DEFAULT '{}',
    target_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL DEFAULT '{}',
    envelope_json TEXT NOT NULL DEFAULT '{}',
    envelope_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    delivery_state TEXT NOT NULL DEFAULT 'pending',
    delivered_at TEXT NOT NULL DEFAULT '',
    acked_at TEXT NOT NULL DEFAULT '',
    failed_at TEXT NOT NULL DEFAULT '',
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_delivery_error TEXT NOT NULL DEFAULT '',
    last_delivery_epoch INTEGER NOT NULL DEFAULT 0
  )`,
  `CREATE INDEX IF NOT EXISTS idx_relay_messages_daemon_created
    ON relay_messages (daemon_id, created_at ASC, id ASC)`,
  `CREATE INDEX IF NOT EXISTS idx_relay_messages_daemon_direction_created
    ON relay_messages (daemon_id, direction, created_at ASC, id ASC)`,
  `CREATE INDEX IF NOT EXISTS idx_relay_messages_pending
    ON relay_messages (daemon_id, direction, acked_at, created_at ASC, id ASC)`,
  `INSERT OR REPLACE INTO relay_meta (key, value)
    VALUES ('schema_version', '${RELAY_SCHEMA_VERSION}')`,
];

export const RELAY_MESSAGE_COLUMNS = [
  "id",
  "daemon_id",
  "direction",
  "kind",
  "source_json",
  "target_json",
  "payload_json",
  "envelope_json",
  "envelope_hash",
  "created_at",
  "delivery_state",
  "delivered_at",
  "acked_at",
  "failed_at",
  "delivery_attempts",
  "last_delivery_error",
  "last_delivery_epoch",
] as const;

export const RELAY_MESSAGE_SELECT = RELAY_MESSAGE_COLUMNS.join(", ");

export interface RelayInstanceRow {
  id: string;
  owner_user_id: string;
  label: string;
  created_at: string;
  last_seen_at: string;
  metadata_json: string;
}

export interface RelayMessageRow {
  id: string;
  daemon_id: string;
  direction: RelayDirection;
  kind: RelayMessageKind;
  source_json: string;
  target_json: string;
  payload_json: string;
  envelope_json: string;
  envelope_hash: string;
  created_at: string;
  delivery_state: RelayDeliveryState;
  delivered_at: string;
  acked_at: string;
  failed_at: string;
  delivery_attempts: number;
  last_delivery_error: string;
  last_delivery_epoch: number;
}

export function normalizeRelayInstanceRow(row: RelayInstanceRow | null | undefined): RelayInstanceRecord | null {
  if (!row) return null;
  return {
    id: String(row.id || ""),
    owner_user_id: String(row.owner_user_id || ""),
    label: String(row.label || ""),
    created_at: String(row.created_at || ""),
    last_seen_at: String(row.last_seen_at || ""),
    metadata: decodeJsonObject(row.metadata_json),
  };
}

export function normalizeRelayMessageRow(
  row: RelayMessageRow | null | undefined,
  options: { idempotent?: boolean } = {},
): StoredRelayMessage | null {
  if (!row) return null;
  const envelope = parseRelayEnvelope(decodeJsonObject(row.envelope_json));
  const deliveryState = normalizeDeliveryState(row.delivery_state);
  return {
    id: String(row.id || ""),
    daemon_id: String(row.daemon_id || ""),
    direction: row.direction,
    kind: row.kind,
    source: decodeJsonObject(row.source_json),
    target: decodeJsonObject(row.target_json),
    payload: decodeJsonObject(row.payload_json),
    envelope,
    envelope_hash: String(row.envelope_hash || ""),
    created_at: String(row.created_at || ""),
    delivery_state: deliveryState,
    delivered_at: String(row.delivered_at || ""),
    acked_at: String(row.acked_at || ""),
    failed_at: String(row.failed_at || ""),
    delivery_attempts: Number(row.delivery_attempts || 0),
    last_delivery_error: String(row.last_delivery_error || ""),
    last_delivery_epoch: Number(row.last_delivery_epoch || 0),
    idempotent: Boolean(options.idempotent),
  };
}

export function relayMessageValues(envelope: RelayEnvelope, direction: RelayDirection): unknown[] {
  const envelopeJson = encodeJsonObject(envelopeToJson(envelope));
  return [
    envelope.id,
    envelope.daemon_id,
    direction,
    envelope.kind,
    encodeJsonObject(endpointToJson(envelope.source)),
    encodeJsonObject(endpointToJson(envelope.target)),
    encodeJsonObject(envelope.payload),
    envelopeJson,
    relayEnvelopeHash(envelope),
    envelope.created_at,
    "pending",
    "",
    "",
    "",
    0,
    "",
    0,
  ];
}

export function relayInstanceValues(record: RelayInstanceRecord): unknown[] {
  return [
    record.id,
    record.owner_user_id,
    record.label,
    record.created_at,
    record.last_seen_at,
    encodeJsonObject(record.metadata || {}),
  ];
}

export function relayEnvelopeHash(envelope: RelayEnvelope): string {
  // Stable fingerprint for idempotency conflict detection. It is intentionally
  // deterministic and portable across Node/Workers; auth/signing belongs to a
  // later phase.
  return stableStringify(envelopeToJson(envelope));
}

export function envelopeToJson(envelope: RelayEnvelope): JsonObject {
  const result: JsonObject = {
    v: envelope.v,
    id: envelope.id,
    daemon_id: envelope.daemon_id,
    source: endpointToJson(envelope.source),
    target: endpointToJson(envelope.target),
    kind: envelope.kind,
    created_at: envelope.created_at,
    payload: envelope.payload,
  };
  if (envelope.trace_id) result.trace_id = envelope.trace_id;
  return result;
}

export function encodeJsonObject(value: JsonObject): string {
  return stableStringify(value || {});
}

export function decodeJsonObject(value: unknown): JsonObject {
  if (!value) return {};
  if (typeof value === "object" && !Array.isArray(value)) return value as JsonObject;
  try {
    const parsed = JSON.parse(String(value));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as JsonObject
      : {};
  } catch {
    return {};
  }
}

export function clampRelayLimit(limit: number | undefined, fallback = 100): number {
  const numeric = Number(limit || fallback);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(1, Math.min(1000, Math.floor(numeric)));
}

export function nowIso(): string {
  return new Date().toISOString();
}

export function normalizeDeliveryState(value: unknown): RelayDeliveryState {
  const state = String(value || "pending").trim() as RelayDeliveryState;
  if (["pending", "delivered", "acked", "failed"].includes(state)) return state;
  return "pending";
}

export function stableStringify(value: unknown): string {
  return JSON.stringify(sortJson(value));
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJson);
  if (!value || typeof value !== "object") return value;
  const input = value as Record<string, unknown>;
  const output: Record<string, unknown> = {};
  for (const key of Object.keys(input).sort()) {
    const item = input[key];
    if (item === undefined) continue;
    output[key] = sortJson(item);
  }
  return output;
}

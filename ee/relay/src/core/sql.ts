import type { JsonObject, RelayEndpoint, RelayEnvelope, RelayMessageKind } from "./protocol.js";
import type { RelayDirection, RelayInstanceRecord, StoredRelayMessage } from "./ports.js";

export const RELAY_SCHEMA_STATEMENTS = [
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
    created_at TEXT NOT NULL,
    delivered_at TEXT NOT NULL DEFAULT '',
    acked_at TEXT NOT NULL DEFAULT ''
  )`,
  `CREATE INDEX IF NOT EXISTS idx_relay_messages_daemon_created
    ON relay_messages (daemon_id, created_at ASC, id ASC)`,
  `CREATE INDEX IF NOT EXISTS idx_relay_messages_daemon_direction_created
    ON relay_messages (daemon_id, direction, created_at ASC, id ASC)`,
];

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
  created_at: string;
  delivered_at: string;
  acked_at: string;
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

export function normalizeRelayMessageRow(row: RelayMessageRow | null | undefined): StoredRelayMessage | null {
  if (!row) return null;
  return {
    id: String(row.id || ""),
    daemon_id: String(row.daemon_id || ""),
    direction: row.direction,
    kind: row.kind,
    source: decodeJsonObject(row.source_json),
    target: decodeJsonObject(row.target_json),
    payload: decodeJsonObject(row.payload_json),
    created_at: String(row.created_at || ""),
    delivered_at: String(row.delivered_at || ""),
    acked_at: String(row.acked_at || ""),
  };
}

export function relayMessageValues(envelope: RelayEnvelope, direction: RelayDirection): unknown[] {
  return [
    envelope.id,
    envelope.daemon_id,
    direction,
    envelope.kind,
    encodeJsonObject(endpointToJson(envelope.source)),
    encodeJsonObject(endpointToJson(envelope.target)),
    encodeJsonObject(envelope.payload),
    envelope.created_at,
    "",
    "",
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

export function encodeJsonObject(value: JsonObject): string {
  return stableStringify(value || {});
}

export function decodeJsonObject(value: unknown): JsonObject {
  if (!value) return {};
  if (typeof value === "object") return value as JsonObject;
  try {
    const parsed = JSON.parse(String(value));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as JsonObject
      : {};
  } catch {
    return {};
  }
}

export function endpointToJson(endpoint: RelayEndpoint): JsonObject {
  const result: JsonObject = {
    kind: endpoint.kind,
    id: endpoint.id,
  };
  if (endpoint.user_id) result.user_id = endpoint.user_id;
  if (endpoint.platform) result.platform = endpoint.platform;
  return result;
}

export function clampRelayLimit(limit: number | undefined, fallback = 100): number {
  const numeric = Number(limit || fallback);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(1, Math.min(1000, Math.floor(numeric)));
}

function stableStringify(value: unknown): string {
  return JSON.stringify(sortJson(value));
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJson);
  if (!value || typeof value !== "object") return value;
  const input = value as Record<string, unknown>;
  const output: Record<string, unknown> = {};
  for (const key of Object.keys(input).sort()) {
    output[key] = sortJson(input[key]);
  }
  return output;
}

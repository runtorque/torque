export const RELAY_PROTOCOL_VERSION = 1 as const;

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

export type RelayEndpointKind = "daemon" | "remote-client" | "channel" | "relay";

export interface RelayEndpoint {
  kind: RelayEndpointKind;
  id: string;
  user_id?: string;
  platform?: string;
}

export type RelayMessageKind =
  | "hello"
  | "ready"
  | "ping"
  | "pong"
  | "snapshot_request"
  | "snapshot"
  | "user_message"
  | "agent_message"
  | "ask"
  | "ask_reply"
  | "ack"
  | "error"
  | "channel_event";

export interface RelayEnvelope<Payload extends JsonObject = JsonObject> {
  v: typeof RELAY_PROTOCOL_VERSION;
  id: string;
  trace_id?: string;
  daemon_id: string;
  source: RelayEndpoint;
  target: RelayEndpoint;
  kind: RelayMessageKind;
  created_at: string;
  payload: Payload;
}

export interface MakeRelayEnvelopeArgs<Payload extends JsonObject = JsonObject> {
  daemon_id: string;
  source: RelayEndpoint;
  target: RelayEndpoint;
  kind: RelayMessageKind;
  payload?: Payload;
  id?: string;
  trace_id?: string;
  created_at?: string;
}

export function newRelayId(prefix = "msg"): string {
  const cryptoRef = globalThis.crypto as Crypto | undefined;
  const randomUuid = cryptoRef && "randomUUID" in cryptoRef
    ? cryptoRef.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  return `${prefix}-${randomUuid}`;
}

export function makeRelayEnvelope<Payload extends JsonObject = JsonObject>(
  args: MakeRelayEnvelopeArgs<Payload>,
): RelayEnvelope<Payload> {
  const daemonId = String(args.daemon_id || "").trim();
  if (!daemonId) throw new Error("daemon_id is required");
  const source = normalizeEndpoint(args.source, "source");
  const target = normalizeEndpoint(args.target, "target");
  return {
    v: RELAY_PROTOCOL_VERSION,
    id: String(args.id || "").trim() || newRelayId(),
    trace_id: String(args.trace_id || "").trim() || undefined,
    daemon_id: daemonId,
    source,
    target,
    kind: args.kind,
    created_at: String(args.created_at || "").trim() || new Date().toISOString(),
    payload: (args.payload || {}) as Payload,
  };
}

export function parseRelayEnvelope(input: unknown): RelayEnvelope {
  if (!input || typeof input !== "object") {
    throw new Error("relay envelope must be an object");
  }
  const raw = input as Record<string, unknown>;
  if (raw.v !== RELAY_PROTOCOL_VERSION) {
    throw new Error(`unsupported relay protocol version: ${String(raw.v)}`);
  }
  const id = asRequiredString(raw.id, "id");
  const daemonId = asRequiredString(raw.daemon_id, "daemon_id");
  const kind = asRequiredString(raw.kind, "kind") as RelayMessageKind;
  const createdAt = asRequiredString(raw.created_at, "created_at");
  const payload = raw.payload && typeof raw.payload === "object"
    ? raw.payload as JsonObject
    : {};
  return {
    v: RELAY_PROTOCOL_VERSION,
    id,
    trace_id: optionalString(raw.trace_id),
    daemon_id: daemonId,
    source: normalizeEndpoint(raw.source, "source"),
    target: normalizeEndpoint(raw.target, "target"),
    kind,
    created_at: createdAt,
    payload,
  };
}

function normalizeEndpoint(value: unknown, fieldName: string): RelayEndpoint {
  if (!value || typeof value !== "object") {
    throw new Error(`${fieldName} endpoint is required`);
  }
  const raw = value as Record<string, unknown>;
  const kind = asRequiredString(raw.kind, `${fieldName}.kind`) as RelayEndpointKind;
  if (!["daemon", "remote-client", "channel", "relay"].includes(kind)) {
    throw new Error(`${fieldName}.kind is invalid: ${kind}`);
  }
  const id = asRequiredString(raw.id, `${fieldName}.id`);
  const result: RelayEndpoint = { kind, id };
  const userId = optionalString(raw.user_id);
  const platform = optionalString(raw.platform);
  if (userId) result.user_id = userId;
  if (platform) result.platform = platform;
  return result;
}

function asRequiredString(value: unknown, fieldName: string): string {
  const text = String(value || "").trim();
  if (!text) throw new Error(`${fieldName} is required`);
  return text;
}

function optionalString(value: unknown): string | undefined {
  const text = String(value || "").trim();
  return text || undefined;
}

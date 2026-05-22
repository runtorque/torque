export const RELAY_PROTOCOL_VERSION = 1 as const;

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

export const RELAY_ENDPOINT_KINDS = [
  "daemon",
  "remote-client",
  "channel",
  "relay",
] as const;

export type RelayEndpointKind = typeof RELAY_ENDPOINT_KINDS[number];

export interface RelayEndpoint {
  kind: RelayEndpointKind;
  id: string;
  user_id?: string;
  platform?: string;
}

// The design artifact called this the V1 local↔cloud protocol. It listed the
// control messages plus channel_event, so keep the complete wire contract here
// even though earlier prose counted twelve kinds.
export const RELAY_MESSAGE_KINDS = [
  "hello",
  "ready",
  "ping",
  "pong",
  "snapshot_request",
  "snapshot",
  "user_message",
  "agent_message",
  "ask",
  "ask_reply",
  "ack",
  "error",
  "channel_event",
] as const;

export type RelayMessageKind = typeof RELAY_MESSAGE_KINDS[number];

export const RELAY_DATA_MESSAGE_KINDS = [
  "snapshot_request",
  "snapshot",
  "user_message",
  "agent_message",
  "ask",
  "ask_reply",
  "channel_event",
] as const satisfies readonly RelayMessageKind[];

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

export type AckPayload = JsonObject & {
  ack_id: string;
  ack_kind?: RelayMessageKind;
  delivery_state?: "delivered" | "acked" | "failed";
  reason?: string;
};

export type ErrorPayload = JsonObject & {
  code: string;
  message: string;
  retryable?: boolean;
  ref_id?: string;
};

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

export class RelayProtocolError extends Error {
  readonly code = "relay_protocol_error";
  constructor(message: string) {
    super(message);
    this.name = "RelayProtocolError";
  }
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
  const daemonId = cleanRequiredString(args.daemon_id, "daemon_id");
  const kind = normalizeMessageKind(args.kind);
  const createdAt = cleanOptionalString(args.created_at, "created_at") || new Date().toISOString();
  validateTimestamp(createdAt, "created_at");
  const envelope: RelayEnvelope<Payload> = {
    v: RELAY_PROTOCOL_VERSION,
    id: cleanOptionalString(args.id, "id") || newRelayId(kind === "ack" ? "ack" : "msg"),
    trace_id: cleanOptionalString(args.trace_id, "trace_id") || undefined,
    daemon_id: daemonId,
    source: normalizeEndpoint(args.source, "source"),
    target: normalizeEndpoint(args.target, "target"),
    kind,
    created_at: createdAt,
    payload: normalizeJsonObject(args.payload || {}, "payload") as Payload,
  };
  validateEnvelopeSemantics(envelope);
  return envelope;
}

export function makeAckEnvelope(args: {
  daemon_id: string;
  source: RelayEndpoint;
  target: RelayEndpoint;
  ack_id: string;
  ack_kind?: RelayMessageKind;
  delivery_state?: AckPayload["delivery_state"];
  reason?: string;
  id?: string;
  trace_id?: string;
  created_at?: string;
}): RelayEnvelope<AckPayload> {
  const payload: AckPayload = {
    ack_id: cleanRequiredString(args.ack_id, "ack_id"),
  };
  if (args.ack_kind) payload.ack_kind = normalizeMessageKind(args.ack_kind);
  if (args.delivery_state) payload.delivery_state = args.delivery_state;
  const reason = cleanOptionalString(args.reason, "reason");
  if (reason) payload.reason = reason;
  return makeRelayEnvelope({
    daemon_id: args.daemon_id,
    source: args.source,
    target: args.target,
    kind: "ack",
    payload,
    id: args.id,
    trace_id: args.trace_id,
    created_at: args.created_at,
  });
}

export function makeErrorEnvelope(args: {
  daemon_id: string;
  source: RelayEndpoint;
  target: RelayEndpoint;
  code: string;
  message: string;
  retryable?: boolean;
  ref_id?: string;
  id?: string;
  trace_id?: string;
  created_at?: string;
}): RelayEnvelope<ErrorPayload> {
  const payload: ErrorPayload = {
    code: cleanRequiredString(args.code, "code"),
    message: cleanRequiredString(args.message, "message"),
  };
  if (typeof args.retryable === "boolean") payload.retryable = args.retryable;
  const refId = cleanOptionalString(args.ref_id, "ref_id");
  if (refId) payload.ref_id = refId;
  return makeRelayEnvelope({
    daemon_id: args.daemon_id,
    source: args.source,
    target: args.target,
    kind: "error",
    payload,
    id: args.id,
    trace_id: args.trace_id,
    created_at: args.created_at,
  });
}

export function parseRelayEnvelope(input: unknown): RelayEnvelope {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new RelayProtocolError("relay envelope must be an object");
  }
  const raw = input as Record<string, unknown>;
  if (raw.v !== RELAY_PROTOCOL_VERSION) {
    throw new RelayProtocolError(`unsupported relay protocol version: ${String(raw.v)}`);
  }
  const createdAt = cleanRequiredString(raw.created_at, "created_at");
  validateTimestamp(createdAt, "created_at");
  const envelope: RelayEnvelope = {
    v: RELAY_PROTOCOL_VERSION,
    id: cleanRequiredString(raw.id, "id"),
    trace_id: cleanOptionalString(raw.trace_id, "trace_id") || undefined,
    daemon_id: cleanRequiredString(raw.daemon_id, "daemon_id"),
    source: normalizeEndpoint(raw.source, "source"),
    target: normalizeEndpoint(raw.target, "target"),
    kind: normalizeMessageKind(raw.kind),
    created_at: createdAt,
    payload: normalizeJsonObject(raw.payload || {}, "payload"),
  };
  validateEnvelopeSemantics(envelope);
  return envelope;
}

export function parseRelayEnvelopeJson(text: string): RelayEnvelope {
  try {
    return parseRelayEnvelope(JSON.parse(text));
  } catch (error) {
    if (error instanceof RelayProtocolError) throw error;
    const message = error instanceof Error ? error.message : String(error);
    throw new RelayProtocolError(`invalid relay JSON: ${message}`);
  }
}

export function isRelayMessageKind(value: unknown): value is RelayMessageKind {
  return RELAY_MESSAGE_KINDS.includes(value as RelayMessageKind);
}

export function ackIdFromEnvelope(envelope: RelayEnvelope): string {
  if (envelope.kind !== "ack") return "";
  return cleanOptionalString((envelope.payload as AckPayload).ack_id, "ack.payload.ack_id") || "";
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

function normalizeEndpoint(value: unknown, fieldName: string): RelayEndpoint {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new RelayProtocolError(`${fieldName} endpoint is required`);
  }
  const raw = value as Record<string, unknown>;
  const kind = cleanRequiredString(raw.kind, `${fieldName}.kind`) as RelayEndpointKind;
  if (!RELAY_ENDPOINT_KINDS.includes(kind)) {
    throw new RelayProtocolError(`${fieldName}.kind is invalid: ${kind}`);
  }
  const result: RelayEndpoint = {
    kind,
    id: cleanRequiredString(raw.id, `${fieldName}.id`),
  };
  const userId = cleanOptionalString(raw.user_id, `${fieldName}.user_id`);
  const platform = cleanOptionalString(raw.platform, `${fieldName}.platform`);
  if (userId) result.user_id = userId;
  if (platform) result.platform = platform;
  return result;
}

function normalizeMessageKind(value: unknown): RelayMessageKind {
  const kind = cleanRequiredString(value, "kind") as RelayMessageKind;
  if (!isRelayMessageKind(kind)) {
    throw new RelayProtocolError(`kind is invalid: ${kind}`);
  }
  return kind;
}

function normalizeJsonObject(value: unknown, fieldName: string): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new RelayProtocolError(`${fieldName} must be an object`);
  }
  assertJsonValue(value, fieldName);
  return value as JsonObject;
}

function assertJsonValue(value: unknown, path: string): void {
  if (value === null) return;
  const kind = typeof value;
  if (kind === "string" || kind === "boolean") return;
  if (kind === "number") {
    if (!Number.isFinite(value)) throw new RelayProtocolError(`${path} must be finite`);
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertJsonValue(item, `${path}[${index}]`));
    return;
  }
  if (kind === "object") {
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      assertJsonValue(item, `${path}.${key}`);
    }
    return;
  }
  throw new RelayProtocolError(`${path} must be JSON-serializable`);
}

function validateEnvelopeSemantics(envelope: RelayEnvelope): void {
  if (envelope.source.kind === "daemon" && envelope.source.id !== envelope.daemon_id) {
    throw new RelayProtocolError("source daemon id must match daemon_id");
  }
  if (envelope.target.kind === "daemon" && envelope.target.id !== envelope.daemon_id) {
    throw new RelayProtocolError("target daemon id must match daemon_id");
  }
  if (envelope.kind === "ack" && !ackIdFromEnvelope(envelope)) {
    throw new RelayProtocolError("ack payload.ack_id is required");
  }
  if (envelope.kind === "ack") {
    const payload = envelope.payload as AckPayload;
    if (payload.ack_kind !== undefined) normalizeMessageKind(payload.ack_kind);
    if (
      payload.delivery_state !== undefined
      && !["delivered", "acked", "failed"].includes(cleanRequiredString(payload.delivery_state, "ack.payload.delivery_state"))
    ) {
      throw new RelayProtocolError(`ack.payload.delivery_state is invalid: ${String(payload.delivery_state)}`);
    }
    cleanOptionalString(payload.reason, "ack.payload.reason");
  }
  if (envelope.kind === "error") {
    const payload = envelope.payload as ErrorPayload;
    cleanRequiredString(payload.code, "error.payload.code");
    cleanRequiredString(payload.message, "error.payload.message");
    cleanOptionalString(payload.ref_id, "error.payload.ref_id");
    if (payload.retryable !== undefined && typeof payload.retryable !== "boolean") {
      throw new RelayProtocolError("error.payload.retryable must be a boolean");
    }
  }
}

function validateTimestamp(value: string, fieldName: string): void {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) {
    throw new RelayProtocolError(`${fieldName} must be an ISO-8601 timestamp`);
  }
}

function cleanRequiredString(value: unknown, fieldName: string): string {
  const text = cleanOptionalString(value, fieldName);
  if (!text) throw new RelayProtocolError(`${fieldName} is required`);
  return text;
}

function cleanOptionalString(value: unknown, fieldName: string): string {
  if (value === undefined || value === null) return "";
  if (typeof value !== "string") {
    throw new RelayProtocolError(`${fieldName} must be a string`);
  }
  return value.trim();
}

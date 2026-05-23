import { RelayError } from "./errors.js";
import { makeRelayEnvelope, type JsonObject, type RelayEnvelope } from "./protocol.js";
import type {
  ClientAuthPrincipal,
  DaemonAuthPrincipal,
  RelayAuthStore,
  RelayClientSessionRecord,
  RelayDaemonCredentialRecord,
  RelayInstanceRecord,
} from "./ports.js";
import { nowIso } from "./sql.js";

export const DAEMON_ATTACH_AUTH_SCHEME = "Torque-Daemon-Signature";
export const DAEMON_ATTACH_AUTH_VERSION = "v1";
export const DAEMON_ATTACH_AUTH_MODE = "signed-attach-v1" as const;
export const CLIENT_SESSION_AUTH_MODE = "session-v1" as const;
export const LOCAL_DEV_AUTH_MODE = "local-dev-unauthenticated" as const;
export const SYNTHETIC_V1_USER_ID = "user";
export const DEFAULT_AUTH_SKEW_SECONDS = 300;
export const EMPTY_SHA256_HEX = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

export type StandaloneAuthMode = typeof LOCAL_DEV_AUTH_MODE | "required";

export interface AuthenticatedDaemonAttachRequest {
  method: string;
  url: URL;
  daemonId: string;
  headers: HeadersLike;
}

export interface HeadersLike {
  get(name: string): string | null;
}

export interface DaemonAttachSignatureParts {
  credentialId: string;
  timestamp: string;
  nonce: string;
  signature: string;
}

export interface AuthOptions {
  now?: Date;
  skewSeconds?: number;
}

export interface ClientAuthRequest {
  daemonId: string;
  headers: HeadersLike;
  now?: Date;
}

export class RelayAuthError extends RelayError {
  constructor(message: string, code = "relay_auth_error", retryable = false) {
    super(message, code, retryable);
    this.name = "RelayAuthError";
  }
}

export function selectStandaloneAuthMode(args: { host?: string; requestedMode?: string }): StandaloneAuthMode {
  const host = String(args.host || "127.0.0.1").trim() || "127.0.0.1";
  const requested = String(args.requestedMode || "").trim() as StandaloneAuthMode | "";
  const loopback = isLoopbackHost(host);
  if (!loopback) {
    if (requested && requested !== "required") {
      throw new RelayAuthError(
        "unauthenticated relay mode is structurally disallowed for non-loopback bind hosts",
        "non_loopback_requires_auth",
      );
    }
    return "required";
  }
  if (!requested) return LOCAL_DEV_AUTH_MODE;
  if (requested === LOCAL_DEV_AUTH_MODE || requested === "required") return requested;
  throw new RelayAuthError(`unknown relay auth mode: ${requested}`, "invalid_auth_mode");
}

export function localDevDaemonPrincipal(daemonId: string): DaemonAuthPrincipal {
  return {
    kind: "daemon",
    daemonId,
    ownerUserId: "local-dev",
    credentialId: "local-dev-unauthenticated",
    authMode: LOCAL_DEV_AUTH_MODE,
  };
}

export function localDevClientPrincipal(): ClientAuthPrincipal {
  return {
    kind: "client",
    ownerUserId: "local-dev",
    sessionId: "local-dev-unauthenticated",
    userId: SYNTHETIC_V1_USER_ID,
    authMode: LOCAL_DEV_AUTH_MODE,
  };
}

export async function authenticateDaemonAttach(
  store: RelayAuthStore & { getInstance(id: string): Promise<RelayInstanceRecord | null> },
  request: AuthenticatedDaemonAttachRequest,
  options: AuthOptions = {},
): Promise<DaemonAuthPrincipal> {
  const daemonId = cleanRequired(request.daemonId, "daemon_id");
  const parts = parseDaemonAttachAuthorization(request.headers.get("authorization"));
  const credential = await store.getDaemonCredential(daemonId, parts.credentialId);
  if (!credential || credential.revoked_at) {
    throw new RelayAuthError("daemon credential is not active", "invalid_daemon_credential");
  }
  if (credential.alg !== "ES256") {
    throw new RelayAuthError(`unsupported daemon credential algorithm: ${credential.alg}`, "unsupported_daemon_alg");
  }
  const now = options.now || new Date();
  assertTimestampFresh(parts.timestamp, now, options.skewSeconds ?? DEFAULT_AUTH_SKEW_SECONDS);
  const canonical = canonicalDaemonAttachString({
    method: request.method,
    path: request.url.pathname,
    daemonId,
    credentialId: parts.credentialId,
    timestamp: parts.timestamp,
    nonce: parts.nonce,
  });
  let verified = false;
  try {
    verified = await verifyEs256(canonical, parts.signature, credential.public_key_jwk);
  } catch {
    verified = false;
  }
  if (!verified) {
    throw new RelayAuthError("daemon attach signature is invalid", "invalid_daemon_signature");
  }
  const nonceHash = await hashSecret(parts.nonce);
  const expiresAt = new Date(now.getTime() + Math.max(1, options.skewSeconds ?? DEFAULT_AUTH_SKEW_SECONDS) * 1000).toISOString();
  const inserted = await store.recordAuthNonce(parts.credentialId, nonceHash, expiresAt, now.toISOString());
  if (!inserted) {
    throw new RelayAuthError("daemon attach nonce has already been used", "replayed_daemon_nonce");
  }
  const instance = await store.getInstance(daemonId);
  if (instance?.owner_user_id && instance.owner_user_id !== credential.owner_user_id) {
    throw new RelayAuthError("daemon owner does not match credential owner", "daemon_owner_mismatch");
  }
  await store.touchDaemonCredential(parts.credentialId, now.toISOString());
  return {
    kind: "daemon",
    daemonId,
    ownerUserId: credential.owner_user_id,
    credentialId: credential.credential_id,
    authMode: DAEMON_ATTACH_AUTH_MODE,
  };
}

export async function authenticateClientSession(
  store: RelayAuthStore & { getInstance(id: string): Promise<RelayInstanceRecord | null> },
  request: ClientAuthRequest,
): Promise<ClientAuthPrincipal> {
  const token = bearerTokenFromHeaders(request.headers) || cookieValue(request.headers, "torque_session");
  if (!token) throw new RelayAuthError("client session token is required", "missing_client_auth");
  const tokenHash = await hashSecret(token);
  const session = await store.getClientSessionByTokenHash(tokenHash);
  const now = request.now || new Date();
  if (!isActiveSession(session, now)) {
    throw new RelayAuthError("client session is not active", "invalid_client_session");
  }
  const instance = await store.getInstance(cleanRequired(request.daemonId, "daemon_id"));
  if (!instance || !instance.owner_user_id) {
    throw new RelayAuthError("daemon is not paired", "daemon_not_paired");
  }
  if (instance.owner_user_id !== session.owner_user_id) {
    throw new RelayAuthError("client session owner does not match daemon owner", "client_owner_mismatch");
  }
  return {
    kind: "client",
    ownerUserId: session.owner_user_id,
    sessionId: session.session_id,
    userId: SYNTHETIC_V1_USER_ID,
    authMode: CLIENT_SESSION_AUTH_MODE,
  };
}

export function canonicalDaemonAttachString(args: {
  method: string;
  path: string;
  daemonId: string;
  credentialId: string;
  timestamp: string;
  nonce: string;
  bodySha256Hex?: string;
}): string {
  return [
    "torque-daemon-attach-v1",
    cleanRequired(args.method, "method").toUpperCase(),
    cleanRequired(args.path, "path"),
    `daemon_id:${cleanRequired(args.daemonId, "daemon_id")}`,
    `credential_id:${cleanRequired(args.credentialId, "credential_id")}`,
    `timestamp:${cleanRequired(args.timestamp, "timestamp")}`,
    `nonce:${cleanRequired(args.nonce, "nonce")}`,
    `body_sha256:${args.bodySha256Hex || EMPTY_SHA256_HEX}`,
  ].join("\n");
}

export function daemonAttachAuthorizationHeader(parts: DaemonAttachSignatureParts): string {
  return `${DAEMON_ATTACH_AUTH_SCHEME} ${DAEMON_ATTACH_AUTH_VERSION} credential_id="${escapeHeader(parts.credentialId)}", timestamp="${escapeHeader(parts.timestamp)}", nonce="${escapeHeader(parts.nonce)}", signature="${escapeHeader(parts.signature)}"`;
}

export function parseDaemonAttachAuthorization(value: string | null): DaemonAttachSignatureParts {
  const text = String(value || "").trim();
  const prefix = `${DAEMON_ATTACH_AUTH_SCHEME} ${DAEMON_ATTACH_AUTH_VERSION}`;
  if (!text.startsWith(prefix)) {
    throw new RelayAuthError("daemon attach Authorization header is required", "missing_daemon_auth");
  }
  const rest = text.slice(prefix.length).trim();
  const fields = new Map<string, string>();
  for (const part of rest.split(/,\s*/)) {
    if (!part) continue;
    const match = part.match(/^([A-Za-z_][A-Za-z0-9_]*)="([^"]*)"$/);
    if (!match) throw new RelayAuthError("daemon attach Authorization header is malformed", "malformed_daemon_auth");
    fields.set(match[1], unescapeHeader(match[2]));
  }
  return {
    credentialId: cleanRequired(fields.get("credential_id") || "", "credential_id"),
    timestamp: cleanRequired(fields.get("timestamp") || "", "timestamp"),
    nonce: cleanRequired(fields.get("nonce") || "", "nonce"),
    signature: cleanRequired(fields.get("signature") || "", "signature"),
  };
}

export async function hashSecret(secret: string): Promise<string> {
  const bytes = new TextEncoder().encode(cleanRequired(secret, "secret"));
  const digest = await cryptoRef().subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function isActiveCredential(record: RelayDaemonCredentialRecord | null | undefined): record is RelayDaemonCredentialRecord {
  return Boolean(record && !record.revoked_at);
}

export function isActiveSession(record: RelayClientSessionRecord | null | undefined, now = new Date()): record is RelayClientSessionRecord {
  if (!record || record.revoked_at) return false;
  const expires = Date.parse(record.expires_at || "");
  return Number.isFinite(expires) && expires > now.getTime();
}

export function sanitizeClientEnvelopeForV1(envelope: RelayEnvelope, principal: ClientAuthPrincipal): RelayEnvelope {
  return makeRelayEnvelope({
    daemon_id: envelope.daemon_id,
    id: envelope.id,
    trace_id: envelope.trace_id,
    created_at: envelope.created_at,
    source: {
      ...envelope.source,
      user_id: principal.userId || SYNTHETIC_V1_USER_ID,
    },
    target: envelope.target,
    kind: envelope.kind,
    payload: envelope.payload,
  });
}

export function authErrorStatus(error: unknown): number {
  if (!(error instanceof RelayAuthError)) return 500;
  if (["daemon_owner_mismatch", "client_owner_mismatch", "non_loopback_requires_auth"].includes(error.code)) return 403;
  return 401;
}

export function makeAuthErrorEnvelope(daemonId: string, targetId: string, error: unknown, targetKind: "daemon" | "remote-client" = "daemon"): RelayEnvelope {
  return makeRelayEnvelope({
    daemon_id: daemonId || "relay",
    source: { kind: "relay", id: "auth" },
    target: targetKind === "daemon"
      ? { kind: "daemon", id: daemonId || targetId || "unknown" }
      : { kind: "remote-client", id: targetId || "unknown", user_id: SYNTHETIC_V1_USER_ID },
    kind: "error",
    payload: {
      code: error instanceof RelayError ? error.code : "relay_auth_error",
      message: error instanceof RelayError ? error.message : String(error || "authentication failed"),
      retryable: false,
    } as JsonObject,
  });
}

async function verifyEs256(canonical: string, signatureBase64Url: string, publicKeyJwk: JsonObject): Promise<boolean> {
  const key = await cryptoRef().subtle.importKey(
    "jwk",
    publicKeyJwk as JsonWebKey,
    { name: "ECDSA", namedCurve: "P-256" },
    false,
    ["verify"],
  );
  const signatureBytes = base64UrlDecode(signatureBase64Url);
  const signature = new Uint8Array(signatureBytes.length);
  signature.set(signatureBytes);
  return cryptoRef().subtle.verify(
    { name: "ECDSA", hash: "SHA-256" },
    key,
    signature.buffer,
    new TextEncoder().encode(canonical),
  );
}

function bearerTokenFromHeaders(headers: HeadersLike): string {
  const value = String(headers.get("authorization") || "").trim();
  const match = value.match(/^Bearer\s+(.+)$/i);
  return match ? match[1].trim() : "";
}

function cookieValue(headers: HeadersLike, name: string): string {
  const raw = String(headers.get("cookie") || "");
  for (const part of raw.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return "";
}

function assertTimestampFresh(timestamp: string, now: Date, skewSeconds: number): void {
  const parsed = Date.parse(timestamp);
  if (!Number.isFinite(parsed)) throw new RelayAuthError("daemon attach timestamp is invalid", "invalid_daemon_timestamp");
  const skewMs = Math.abs(now.getTime() - parsed);
  if (skewMs > Math.max(1, skewSeconds) * 1000) {
    throw new RelayAuthError("daemon attach timestamp is outside the allowed skew window", "expired_daemon_timestamp");
  }
}

function isLoopbackHost(host: string): boolean {
  const value = String(host || "").trim().toLowerCase();
  if (!value) return true;
  if (["localhost", "127.0.0.1", "::1", "[::1]"].includes(value)) return true;
  if (value.endsWith(".localhost")) return true;
  if (value.startsWith("127.")) return true;
  return false;
}

function base64UrlDecode(value: string): Uint8Array {
  const text = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
  const padded = text + "=".repeat((4 - (text.length % 4)) % 4);
  const bufferCtor = (globalThis as unknown as { Buffer?: typeof Buffer }).Buffer;
  if (bufferCtor) return new Uint8Array(bufferCtor.from(padded, "base64"));
  const binary = globalThis.atob(padded);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

function cryptoRef(): Crypto {
  if (!globalThis.crypto?.subtle) {
    throw new RelayAuthError("WebCrypto is required for relay auth", "webcrypto_unavailable");
  }
  return globalThis.crypto;
}

function cleanRequired(value: string | undefined | null, name: string): string {
  const text = String(value || "").trim();
  if (!text) throw new RelayAuthError(`${name} is required`, "relay_auth_validation_error");
  return text;
}

function escapeHeader(value: string): string {
  return String(value || "").replace(/\\/g, "\\\\").replace(/"/g, "\\\"");
}

function unescapeHeader(value: string): string {
  return String(value || "").replace(/\\"/g, '"').replace(/\\\\/g, "\\");
}

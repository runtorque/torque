/**
 * Daemon-mediated mint of a single-use client establish code (Channels go-live
 * b2). This is the ONLY way to provision a relay device link from the daemon —
 * it rides the AUTHENTICATED daemon WS (`mint_client_establish_code`), never a
 * public/admin HTTP surface (b1 stays rejected).
 *
 * Because the daemon WS handler is public-auth-adjacent, the mint enforces every
 * auth invariant SERVER-SIDE before creating a code:
 *
 *   1. OWNER FROM ATTACH: the owner is taken from the authenticated daemon
 *      principal (the signed-attach credential the relay already verified) —
 *      NEVER from any client/daemon-supplied field in the request payload.
 *   2. CODE_HASH ONLY: only the SHA-256 hash of the code is persisted (the
 *      existing establish-code model); the raw code is returned to the daemon
 *      exactly once and is never persisted raw, with a SHORT TTL (bearer cred).
 *   3. RATE-LIMIT: a per-daemon mint rate-limit bounds how many codes a single
 *      authenticated daemon can mint per window (the local-confirmation gesture
 *      lives on the daemon command; see invariant 3 there).
 *   4. REPLAY + FENCING: the request carries a nonce the relay records like the
 *      signed-attach nonce (a replayed mint request is rejected — the P4 hole);
 *      and the request is rejected if the daemon session is stale/fenced or its
 *      credential was rotated/revoked (the P5 fencing-epoch monotonicity hole).
 */
import { RelayAuthError, hashSecret } from "./auth.js";
import { makeRelayEnvelope, type JsonObject, type RelayEnvelope } from "./protocol.js";
import type { DaemonAuthPrincipal, RelayStore } from "./ports.js";

export const MINT_CLIENT_ESTABLISH_CODE_KIND = "mint_client_establish_code" as const;
export const MINT_CLIENT_ESTABLISH_CODE_RESULT_KIND = "mint_client_establish_code_result" as const;

// Short TTL: the minted code is a single-use BEARER credential carried in a
// QR/link, so it must expire quickly. Matches the seed helper's establish TTL.
export const DEFAULT_ESTABLISH_CODE_TTL_MS = 15 * 60 * 1000;
// Per-daemon mint rate-limit: at most this many codes minted within the window.
export const DEFAULT_MINT_RATE_LIMIT = 5;
export const DEFAULT_MINT_RATE_WINDOW_MS = 10 * 60 * 1000;
// Namespace prefix so a mint nonce can never collide with (or be replayed as) a
// signed-attach nonce recorded under the same credential id.
const MINT_NONCE_NAMESPACE = "mint-establish-code:";

export interface MintCoordinatorLike {
  isCurrentDaemonConnection(daemonId: string, connectionId: string, epoch?: number): Promise<boolean> | boolean;
}

export interface MintEstablishCodeOptions {
  now?: Date;
  codeTtlMs?: number;
  rateLimit?: number;
  rateWindowMs?: number;
}

export interface MintEstablishCodeArgs {
  store: RelayStore;
  // Connection-level fencing: the daemon WS connection that carried the request
  // must still be the current owner connection (rejects a stale/replaced socket).
  coordinator?: MintCoordinatorLike;
  daemonId: string;
  // SERVER-SIDE owner identity — the verified daemon attach principal.
  principal: DaemonAuthPrincipal;
  connectionId: string;
  epoch: number;
  // The mint request payload (carries `nonce` and an optional `label`). The owner
  // is NEVER read from here.
  payload: JsonObject;
  options?: MintEstablishCodeOptions;
}

export interface MintEstablishCodeResult {
  /** Raw single-use code — returned to the daemon EXACTLY ONCE, never persisted. */
  code: string;
  codeId: string;
  ownerUserId: string;
  expiresAt: string;
}

/**
 * Mint a single-use client establish code for the authenticated daemon. Throws a
 * {@link RelayAuthError} (never returns) when any invariant fails so the caller
 * can surface a typed error envelope and never minting on a rejected request.
 */
export async function mintClientEstablishCode(args: MintEstablishCodeArgs): Promise<MintEstablishCodeResult> {
  const options = args.options || {};
  const now = options.now || new Date();
  const daemonId = cleanRequired(args.daemonId, "daemon_id");
  // [1] Owner is taken from the verified attach principal — never the payload.
  const ownerUserId = cleanRequired(args.principal?.ownerUserId, "owner_user_id");
  const credentialId = cleanRequired(args.principal?.credentialId, "credential_id");
  const signedAttach = args.principal?.authMode === "signed-attach-v1";

  const payload = (args.payload || {}) as JsonObject;
  const nonce = cleanRequired(typeof payload.nonce === "string" ? payload.nonce : "", "nonce");
  const label = typeof payload.label === "string" ? payload.label.trim() : "";

  // [4/P5] Connection-level fencing: reject a stale/replaced/superseded socket.
  if (args.coordinator) {
    const current = await args.coordinator.isCurrentDaemonConnection(daemonId, args.connectionId, args.epoch);
    if (!current) {
      throw new RelayAuthError("daemon connection is stale or fenced", "stale_daemon_connection");
    }
  }

  // [1] Owner-scoping against the paired instance.
  const instance = await args.store.getInstance(daemonId);
  if (!instance || !instance.owner_user_id) {
    throw new RelayAuthError("daemon is not paired", "daemon_not_paired");
  }
  if (instance.owner_user_id !== ownerUserId) {
    throw new RelayAuthError("daemon owner does not match the authenticated session", "daemon_owner_mismatch");
  }

  // [4/P5] Credential rotation/revocation + fencing-epoch monotonicity. Skipped
  // for loopback local-dev (no credential rows exist), where the connection-level
  // fencing above is the authority; the security-sensitive non-loopback path is
  // always signed-attach.
  if (signedAttach) {
    const credential = await args.store.getDaemonCredential(daemonId, credentialId);
    if (!credential || credential.revoked_at) {
      throw new RelayAuthError("daemon credential is not active", "invalid_daemon_credential");
    }
    if (credential.owner_user_id !== ownerUserId) {
      throw new RelayAuthError("daemon credential owner does not match the authenticated session", "daemon_owner_mismatch");
    }
    if (instance.active_credential_id && instance.active_credential_id !== credentialId) {
      throw new RelayAuthError("daemon credential has been superseded by a rotation", "superseded_daemon_credential");
    }
    if (Number(instance.fencing_epoch || 0) > Number(args.epoch || 0)) {
      throw new RelayAuthError("daemon fencing epoch is stale", "stale_fencing_epoch");
    }
  }

  // [3] Per-daemon mint rate-limit (count codes minted within the window).
  const rateWindowMs = clampPositive(options.rateWindowMs, DEFAULT_MINT_RATE_WINDOW_MS);
  const rateLimit = clampPositive(options.rateLimit, DEFAULT_MINT_RATE_LIMIT);
  const sinceIso = new Date(now.getTime() - rateWindowMs).toISOString();
  const recent = await args.store.countRecentEstablishCodes(daemonId, sinceIso);
  if (recent >= rateLimit) {
    throw new RelayAuthError("establish code mint rate limit exceeded for this daemon", "mint_rate_limited", true);
  }

  // [4/P4] Replay protection: record the (namespaced) nonce like the attach nonce.
  // A replayed mint request reuses the same nonce → INSERT OR IGNORE no-ops → reject.
  const codeTtlMs = clampPositive(options.codeTtlMs, DEFAULT_ESTABLISH_CODE_TTL_MS);
  const nonceHash = await hashSecret(`${MINT_NONCE_NAMESPACE}${nonce}`);
  // Keep the nonce reserved at least as long as the minted code can live, so a
  // replay cannot ride a still-valid code.
  const nonceExpiresAt = new Date(now.getTime() + Math.max(rateWindowMs, codeTtlMs) + 1).toISOString();
  const inserted = await args.store.recordAuthNonce(credentialId, nonceHash, nonceExpiresAt, now.toISOString());
  if (!inserted) {
    throw new RelayAuthError("mint request nonce has already been used", "replayed_mint_nonce");
  }

  // [4/P5] FINAL connection-level fencing re-check, immediately before the mint —
  // closes a check-then-act TOCTOU. On the cloudflare DO path D1 is an external
  // binding (not DO transactional storage), so the input gate does NOT close on
  // the ungated D1 awaits above (getInstance/getDaemonCredential/count/recordNonce):
  // a superseding same-owner attach can land mid-flight after the initial check at
  // the top. This re-check is the LAST gate before createClientEstablishCode, so a
  // mint that already passed the first check with a now-stale epoch is still fenced.
  // Placed AFTER the nonce burn so replay protection is NOT bypassed (the nonce is
  // already consumed; a same-nonce retry still fails). Same guard/throw as the first.
  if (args.coordinator) {
    const stillCurrent = await args.coordinator.isCurrentDaemonConnection(daemonId, args.connectionId, args.epoch);
    if (!stillCurrent) {
      throw new RelayAuthError("daemon connection is stale or fenced", "stale_daemon_connection");
    }
  }

  // [2] Mint: persist ONLY the code hash; the raw code is returned once below.
  const code = randomCode();
  const codeId = `establish-${randomUuid()}`;
  const expiresAt = new Date(now.getTime() + codeTtlMs).toISOString();
  await args.store.createClientEstablishCode({
    id: codeId,
    code_hash: await hashSecret(code),
    owner_user_id: ownerUserId,
    daemon_id: daemonId,
    label,
    created_at: now.toISOString(),
    expires_at: expiresAt,
    consumed_at: "",
    revoked_at: "",
    metadata: { minted_via: "daemon-ws", credential_id: credentialId },
  });

  return { code, codeId, ownerUserId, expiresAt };
}

/** Build the relay→daemon response carrying the raw code exactly once. */
export function makeMintResultEnvelope(args: {
  daemonId: string;
  relayId: string;
  result: MintEstablishCodeResult;
  refId: string;
  traceId?: string;
}): RelayEnvelope {
  return makeRelayEnvelope({
    daemon_id: args.daemonId,
    source: { kind: "relay", id: args.relayId || "relay" },
    target: { kind: "daemon", id: args.daemonId },
    kind: MINT_CLIENT_ESTABLISH_CODE_RESULT_KIND,
    trace_id: args.traceId || undefined,
    payload: {
      ref_id: args.refId || "",
      code: args.result.code,
      code_id: args.result.codeId,
      owner_user_id: args.result.ownerUserId,
      expires_at: args.result.expiresAt,
    } as JsonObject,
  });
}

function randomCode(): string {
  const bytes = new Uint8Array(32);
  cryptoRef().getRandomValues(bytes);
  const bufferCtor = (globalThis as unknown as { Buffer?: typeof Buffer }).Buffer;
  const base64 = bufferCtor
    ? bufferCtor.from(bytes).toString("base64")
    : btoa(String.fromCharCode(...bytes));
  return base64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function randomUuid(): string {
  const ref = cryptoRef();
  return "randomUUID" in ref ? ref.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function cryptoRef(): Crypto {
  if (!globalThis.crypto?.getRandomValues) {
    throw new RelayAuthError("WebCrypto is required to mint establish codes", "webcrypto_unavailable");
  }
  return globalThis.crypto;
}

function clampPositive(value: number | undefined, fallback: number): number {
  const numeric = Math.floor(Number(value ?? fallback));
  if (!Number.isFinite(numeric) || numeric <= 0) return fallback;
  return numeric;
}

function cleanRequired(value: string | undefined | null, name: string): string {
  const text = String(value || "").trim();
  if (!text) throw new RelayAuthError(`${name} is required`, "relay_mint_validation_error");
  return text;
}

import type { RelayStore } from "../../src/core/ports.js";
import {
  canonicalDaemonAttachString,
  daemonAttachAuthorizationHeader,
  hashSecret,
  type DaemonAttachSignatureParts,
} from "../../src/core/auth.js";

export type DaemonCredentialFixture = {
  credentialId: string;
  daemonId: string;
  ownerUserId: string;
  publicKeyJwk: JsonWebKey;
  privateKey: CryptoKey;
};

export async function createDaemonCredentialFixture(
  store: RelayStore,
  args: { daemonId?: string; ownerUserId?: string; credentialId?: string; label?: string } = {},
): Promise<DaemonCredentialFixture> {
  const daemonId = args.daemonId || "daemon-1";
  const ownerUserId = args.ownerUserId || "owner-1";
  const credentialId = args.credentialId || `cred-${crypto.randomUUID()}`;
  const keyPair = await crypto.subtle.generateKey(
    { name: "ECDSA", namedCurve: "P-256" },
    true,
    ["sign", "verify"],
  ) as CryptoKeyPair;
  const publicKeyJwk = await crypto.subtle.exportKey("jwk", keyPair.publicKey);
  const now = "2026-05-23T00:00:00.000Z";
  await store.createDaemonCredential({
    credential_id: credentialId,
    daemon_id: daemonId,
    owner_user_id: ownerUserId,
    public_key_jwk: publicKeyJwk as any,
    alg: "ES256",
    created_at: now,
    last_used_at: "",
    revoked_at: "",
    metadata: {},
  });
  await store.upsertInstance({
    id: daemonId,
    owner_user_id: ownerUserId,
    label: args.label || daemonId,
    created_at: now,
    last_seen_at: now,
    fencing_epoch: 0,
    active_credential_id: credentialId,
    coordination_updated_at: now,
    metadata: {},
  });
  return { credentialId, daemonId, ownerUserId, publicKeyJwk, privateKey: keyPair.privateKey };
}

export async function signedDaemonAttachHeader(
  fixture: DaemonCredentialFixture,
  args: { path?: string; timestamp?: string; nonce?: string } = {},
): Promise<string> {
  const timestamp = args.timestamp || new Date().toISOString();
  const nonce = args.nonce || `nonce-${crypto.randomUUID()}`;
  const canonical = canonicalDaemonAttachString({
    method: "GET",
    path: args.path || `/v1/daemon/${encodeURIComponent(fixture.daemonId)}/ws`,
    daemonId: fixture.daemonId,
    credentialId: fixture.credentialId,
    timestamp,
    nonce,
  });
  const signature = await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" },
    fixture.privateKey,
    new TextEncoder().encode(canonical),
  );
  const parts: DaemonAttachSignatureParts = {
    credentialId: fixture.credentialId,
    timestamp,
    nonce,
    signature: base64Url(new Uint8Array(signature)),
  };
  return daemonAttachAuthorizationHeader(parts);
}

export async function createClientSessionFixture(
  store: RelayStore,
  args: { ownerUserId?: string; sessionId?: string; token?: string; expiresAt?: string } = {},
): Promise<{ token: string; sessionId: string; ownerUserId: string }> {
  const token = args.token || `client-${crypto.randomUUID()}`;
  const sessionId = args.sessionId || `session-${crypto.randomUUID()}`;
  const ownerUserId = args.ownerUserId || "owner-1";
  await store.createClientSession({
    session_id: sessionId,
    token_hash: await hashSecret(token),
    owner_user_id: ownerUserId,
    created_at: "2026-05-23T00:00:00.000Z",
    expires_at: args.expiresAt || "2099-01-01T00:00:00.000Z",
    revoked_at: "",
    metadata: {},
  });
  return { token, sessionId, ownerUserId };
}

function base64Url(bytes: Uint8Array): string {
  return Buffer.from(bytes).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

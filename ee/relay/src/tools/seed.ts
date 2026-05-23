/**
 * Path-A provisioning seed helper for the deployed Cloudflare relay.
 *
 * The deployed Worker exposes no `/v1/pair` or `/v1/admin/*` surface, so a daemon
 * credential and a client session must be seeded directly into the remote D1.
 * This one-shot generates that material locally and emits a `.sql` file you apply
 * with `wrangler d1 execute torque-relay --remote --file <path>`.
 *
 * Secret discipline (DO NOT regress):
 *   - The CLIENT SESSION raw token is generated locally, printed to stdout exactly
 *     ONCE for out-of-band hand-off, and NEVER written to a file or passed on a
 *     command line. Only its SHA-256 hash (computed by the same `hashSecret` the
 *     relay uses at auth time) goes into the emitted SQL.
 *   - The DAEMON PRIVATE KEY is written to a local 0600 PEM file and NEVER printed.
 *     Only the PUBLIC JWK (non-secret) goes into the emitted SQL.
 *   - The emitted `.sql` file therefore contains no secrets (hash + public JWK only).
 *
 * Usage (build first: `npm run build`):
 *   node dist/src/tools/seed.js --owner <owner_user_id> --daemon <daemon_id> \
 *     [--credential-id <id>] [--label <label>] [--session-expires <iso>] \
 *     [--key-out <path>] [--sql-out <path>]
 */
import { hashSecret } from "../core/auth.js";
import {
  RELAY_INSTANCE_COLUMNS,
  nowIso,
  relayClientSessionValues,
  relayDaemonCredentialValues,
  relayInstanceValues,
} from "../core/sql.js";
import type { JsonObject } from "../core/protocol.js";
import type {
  RelayClientSessionRecord,
  RelayDaemonCredentialRecord,
  RelayInstanceRecord,
} from "../core/ports.js";

export const RELAY_DB_NAME = "torque-relay";

// Column orders MUST stay aligned with migrations/0001_relay.sql and the value
// helpers in core/sql.ts (which produce the values in exactly this order).
export const RELAY_CLIENT_SESSION_COLUMNS = [
  "session_id",
  "token_hash",
  "owner_user_id",
  "created_at",
  "expires_at",
  "revoked_at",
  "metadata_json",
] as const;

export const RELAY_DAEMON_CREDENTIAL_COLUMNS = [
  "credential_id",
  "daemon_id",
  "owner_user_id",
  "public_key_jwk_json",
  "alg",
  "created_at",
  "last_used_at",
  "revoked_at",
  "metadata_json",
] as const;

const DEFAULT_SESSION_TTL_MS = 24 * 60 * 60 * 1000;

export interface ClientSessionSeed {
  /** Raw bearer/cookie token — print ONCE, never persist. */
  rawToken: string;
  record: RelayClientSessionRecord;
  /** INSERT carrying only the token HASH (no raw token). */
  statement: string;
}

export interface DaemonCredentialSeed {
  record: RelayDaemonCredentialRecord;
  instance: RelayInstanceRecord;
  /** [credential INSERT, instance INSERT] — public JWK only, no private key. */
  statements: string[];
}

export interface DaemonKeyMaterial {
  /** Non-secret public key, stored by the relay. */
  publicKeyJwk: JsonObject;
  /** PKCS#8 PEM private key — write 0600, never log/commit. */
  privateKeyPem: string;
}

/** SQL literal with single-quote doubling; numbers inline bare. */
export function sqlLiteral(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "'" + String(value ?? "").replace(/'/g, "''") + "'";
}

export function insertStatement(table: string, columns: readonly string[], values: unknown[]): string {
  return `INSERT INTO ${table} (${columns.join(", ")}) VALUES (${values.map(sqlLiteral).join(", ")});`;
}

export async function buildClientSessionSeed(args: {
  ownerUserId: string;
  rawToken?: string;
  sessionId?: string;
  expiresAt?: string;
  now?: string;
}): Promise<ClientSessionSeed> {
  const ownerUserId = required(args.ownerUserId, "owner_user_id");
  const rawToken = args.rawToken || randomToken();
  const now = args.now || nowIso();
  const record: RelayClientSessionRecord = {
    session_id: args.sessionId || `session-${crypto.randomUUID()}`,
    // Same trim-then-SHA256 the relay applies at auth time (core/auth.ts
    // hashSecret); the seeded hash must match or the session never authenticates.
    token_hash: await hashSecret(rawToken),
    owner_user_id: ownerUserId,
    created_at: now,
    expires_at: args.expiresAt || new Date(parseTime(now) + DEFAULT_SESSION_TTL_MS).toISOString(),
    revoked_at: "",
    metadata: {},
  };
  const statement = insertStatement(
    "relay_client_sessions",
    RELAY_CLIENT_SESSION_COLUMNS,
    relayClientSessionValues(record),
  );
  return { rawToken, record, statement };
}

export function buildDaemonCredentialSeed(args: {
  daemonId: string;
  ownerUserId: string;
  publicKeyJwk: JsonObject;
  credentialId?: string;
  label?: string;
  now?: string;
}): DaemonCredentialSeed {
  const daemonId = required(args.daemonId, "daemon_id");
  const ownerUserId = required(args.ownerUserId, "owner_user_id");
  const now = args.now || nowIso();
  const credentialId = args.credentialId || `cred-${crypto.randomUUID()}`;
  const record: RelayDaemonCredentialRecord = {
    credential_id: credentialId,
    daemon_id: daemonId,
    owner_user_id: ownerUserId,
    public_key_jwk: args.publicKeyJwk || {},
    alg: "ES256",
    created_at: now,
    last_used_at: "",
    revoked_at: "",
    metadata: {},
  };
  const instance: RelayInstanceRecord = {
    id: daemonId,
    owner_user_id: ownerUserId,
    label: args.label || daemonId,
    created_at: now,
    last_seen_at: now,
    fencing_epoch: 0,
    active_credential_id: credentialId,
    coordination_updated_at: now,
    metadata: { paired_by: "seed-helper" },
  };
  return {
    record,
    instance,
    statements: [
      insertStatement("relay_daemon_credentials", RELAY_DAEMON_CREDENTIAL_COLUMNS, relayDaemonCredentialValues(record)),
      insertStatement("relay_instances", RELAY_INSTANCE_COLUMNS, relayInstanceValues(instance)),
    ],
  };
}

export async function generateDaemonKeyMaterial(): Promise<DaemonKeyMaterial> {
  const keyPair = (await crypto.subtle.generateKey(
    { name: "ECDSA", namedCurve: "P-256" },
    true,
    ["sign", "verify"],
  )) as CryptoKeyPair;
  const publicKeyJwk = (await crypto.subtle.exportKey("jwk", keyPair.publicKey)) as unknown as JsonObject;
  const pkcs8 = await crypto.subtle.exportKey("pkcs8", keyPair.privateKey);
  return { publicKeyJwk, privateKeyPem: pemEncode("PRIVATE KEY", new Uint8Array(pkcs8)) };
}

function pemEncode(label: string, bytes: Uint8Array): string {
  const b64 = Buffer.from(bytes).toString("base64");
  const lines = b64.match(/.{1,64}/g) || [b64];
  return `-----BEGIN ${label}-----\n${lines.join("\n")}\n-----END ${label}-----\n`;
}

function randomToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Buffer.from(bytes).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function parseTime(iso: string): number {
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : Date.now();
}

function required(value: string | undefined, name: string): string {
  const text = String(value || "").trim();
  if (!text) throw new Error(`${name} is required`);
  return text;
}

function parseArgs(argv: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) {
      out[key] = "true";
    } else {
      out[key] = next;
      i += 1;
    }
  }
  return out;
}

async function runCli(argv: string[]): Promise<void> {
  const { writeFileSync } = await import("node:fs");
  const args = parseArgs(argv);
  const ownerUserId = required(args.owner, "--owner");
  const daemonId = required(args.daemon, "--daemon");
  const keyOut = args["key-out"] || `./torque-daemon-${daemonId}.key.pem`;
  const sqlOut = args["sql-out"] || `./torque-relay-seed.sql`;

  const key = await generateDaemonKeyMaterial();
  const daemonSeed = buildDaemonCredentialSeed({
    daemonId,
    ownerUserId,
    publicKeyJwk: key.publicKeyJwk,
    credentialId: args["credential-id"],
    label: args.label,
  });
  const sessionSeed = await buildClientSessionSeed({ ownerUserId, expiresAt: args["session-expires"] });

  // Private key: 0600, never printed.
  writeFileSync(keyOut, key.privateKeyPem, { mode: 0o600 });
  // SQL: hash + public JWK only — no secrets.
  writeFileSync(sqlOut, [...daemonSeed.statements, sessionSeed.statement].join("\n") + "\n", { mode: 0o600 });

  process.stdout.write(
    [
      "# Seed written. The .sql file contains ONLY the hashed session token + PUBLIC key JWK (no secrets).",
      `# 1. Apply to the deployed D1:`,
      `#    npx wrangler d1 execute ${RELAY_DB_NAME} --remote --file ${sqlOut}`,
      `# 2. Daemon private key (0600, keep local, NEVER commit): ${keyOut}`,
      `# 3. Connector config: TORQUE_EE_DAEMON_CREDENTIAL_ID=${daemonSeed.record.credential_id}`,
      `#    private_key_path=${keyOut} (mode 0600), TORQUE_EE_DAEMON_ID=${daemonId}`,
      `#    TORQUE_EE_RELAY_URL=wss://relay.runtorque.com`,
      "",
      "# >>> CLIENT SESSION TOKEN — shown ONCE. Copy now; hand to the device out-of-band. <<<",
      sessionSeed.rawToken,
      "",
    ].join("\n"),
  );
}

const isMain = process.argv[1] && (await import("node:url")).fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) {
  await runCli(process.argv.slice(2));
}

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
  relayClientEstablishCodeValues,
  relayDaemonCredentialValues,
  relayInstanceValues,
} from "../core/sql.js";
import type { JsonObject } from "../core/protocol.js";
import type {
  RelayClientEstablishCodeRecord,
  RelayDaemonCredentialRecord,
  RelayInstanceRecord,
} from "../core/ports.js";

export const RELAY_DB_NAME = "torque-relay";

// Column orders MUST stay aligned with the migrations and the value helpers in
// core/sql.ts (which produce the values in exactly this order).
export const RELAY_CLIENT_ESTABLISH_CODE_COLUMNS = [
  "id",
  "code_hash",
  "owner_user_id",
  "daemon_id",
  "label",
  "created_at",
  "expires_at",
  "consumed_at",
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

// The establish code is short-lived (the QR/link is scanned promptly); the
// minted session it redeems for gets its own longer lifetime server-side.
const DEFAULT_CODE_TTL_MS = 15 * 60 * 1000;

export interface ClientEstablishCodeSeed {
  /** Raw single-use code — print ONCE for the QR/link, never persist. */
  rawCode: string;
  record: RelayClientEstablishCodeRecord;
  /** INSERT carrying only the code HASH (no raw code). */
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

export async function buildClientEstablishCodeSeed(args: {
  ownerUserId: string;
  daemonId?: string;
  rawCode?: string;
  id?: string;
  label?: string;
  expiresAt?: string;
  now?: string;
}): Promise<ClientEstablishCodeSeed> {
  const ownerUserId = required(args.ownerUserId, "owner_user_id");
  const rawCode = args.rawCode || randomToken();
  const now = args.now || nowIso();
  const record: RelayClientEstablishCodeRecord = {
    id: args.id || `establish-${crypto.randomUUID()}`,
    // Same trim-then-SHA256 the relay applies when redeeming the code at
    // /establish (core/auth.ts hashSecret); the seeded hash must match exactly.
    code_hash: await hashSecret(rawCode),
    owner_user_id: ownerUserId,
    daemon_id: args.daemonId || "",
    label: args.label || "",
    created_at: now,
    expires_at: args.expiresAt || new Date(parseTime(now) + DEFAULT_CODE_TTL_MS).toISOString(),
    consumed_at: "",
    revoked_at: "",
    metadata: {},
  };
  const statement = insertStatement(
    "relay_client_establish_codes",
    RELAY_CLIENT_ESTABLISH_CODE_COLUMNS,
    relayClientEstablishCodeValues(record),
  );
  return { rawCode, record, statement };
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
  const codeSeed = await buildClientEstablishCodeSeed({
    ownerUserId,
    daemonId,
    expiresAt: args["code-expires"],
  });

  // Private key: 0600, never printed.
  writeFileSync(keyOut, key.privateKeyPem, { mode: 0o600 });
  // SQL: code HASH + public JWK only — no secrets (no raw code, no session token).
  writeFileSync(sqlOut, [...daemonSeed.statements, codeSeed.statement].join("\n") + "\n", { mode: 0o600 });

  const establishUrl = `https://relay.runtorque.com/establish?code=${encodeURIComponent(codeSeed.rawCode)}`;
  process.stdout.write(
    [
      "# Seed written. The .sql file contains ONLY the hashed establish code + PUBLIC key JWK (no secrets).",
      `# 1. Apply to the deployed D1:`,
      `#    npx wrangler d1 execute ${RELAY_DB_NAME} --remote --file ${sqlOut}`,
      `# 2. Daemon private key (0600, keep local, NEVER commit): ${keyOut}`,
      `# 3. Connector config: TORQUE_EE_DAEMON_CREDENTIAL_ID=${daemonSeed.record.credential_id}`,
      `#    private_key_path=${keyOut} (mode 0600), TORQUE_EE_DAEMON_ID=${daemonId}`,
      `#    TORQUE_EE_RELAY_URL=wss://relay.runtorque.com`,
      "",
      "# >>> SINGLE-USE ESTABLISH LINK — shown ONCE, short-lived. QR/hand to the device; <<<",
      "# >>> opening it mints the session cookie. The session token never appears here.   <<<",
      establishUrl,
      "",
    ].join("\n"),
  );
}

const isMain = process.argv[1] && (await import("node:url")).fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) {
  await runCli(process.argv.slice(2));
}

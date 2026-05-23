import assert from "node:assert/strict";
import test from "node:test";

import { hashSecret } from "../src/core/auth.js";
import {
  RELAY_DB_NAME,
  buildClientSessionSeed,
  buildDaemonCredentialSeed,
  generateDaemonKeyMaterial,
  insertStatement,
  sqlLiteral,
} from "../src/tools/seed.js";

test("client session seed hash EXACTLY matches the relay's hashSecret", async () => {
  // hashSecret trims then SHA-256; the seeded hash must be byte-identical to what
  // the relay computes from the token at auth time, or the session never authenticates.
  const seed = await buildClientSessionSeed({ ownerUserId: "owner-1", rawToken: "  spaced-token  " });
  assert.equal(seed.record.token_hash, await hashSecret("  spaced-token  "));
});

test("client session seed never persists the raw token in the emitted SQL", async () => {
  const seed = await buildClientSessionSeed({ ownerUserId: "owner-1" });
  assert.ok(seed.rawToken.length >= 16, "generated token should have real entropy");
  assert.ok(!seed.statement.includes(seed.rawToken), "raw token must not appear in SQL");
  assert.ok(seed.statement.includes(seed.record.token_hash), "SQL must carry the hash");
  assert.equal(seed.record.token_hash, await hashSecret(seed.rawToken));
});

test("generated client tokens are unique", async () => {
  const a = await buildClientSessionSeed({ ownerUserId: "owner-1" });
  const b = await buildClientSessionSeed({ ownerUserId: "owner-1" });
  assert.notEqual(a.rawToken, b.rawToken);
  assert.notEqual(a.record.token_hash, b.record.token_hash);
});

test("daemon credential seed stores the PUBLIC JWK and stamps the instance owner", async () => {
  const key = await generateDaemonKeyMaterial();
  const seed = buildDaemonCredentialSeed({ daemonId: "daemon-1", ownerUserId: "owner-1", publicKeyJwk: key.publicKeyJwk });
  assert.equal(seed.record.alg, "ES256");
  assert.equal(seed.record.daemon_id, "daemon-1");
  assert.equal(seed.instance.owner_user_id, "owner-1");
  assert.equal(seed.instance.active_credential_id, seed.record.credential_id);
  assert.equal(seed.statements.length, 2);
  // The PUBLIC JWK must never carry the private scalar "d".
  assert.ok(!Object.keys(seed.record.public_key_jwk).includes("d"), "public JWK must not contain private key material");
});

test("generated key material is an exportable P-256 PKCS#8 PEM with no inline private scalar", async () => {
  const key = await generateDaemonKeyMaterial();
  assert.equal((key.publicKeyJwk as { crv?: string }).crv, "P-256");
  assert.ok(key.privateKeyPem.startsWith("-----BEGIN PRIVATE KEY-----"));
  assert.ok(!Object.keys(key.publicKeyJwk).includes("d"));
});

test("sqlLiteral escapes single quotes and inlines finite numbers bare", () => {
  assert.equal(sqlLiteral("o'brien"), "'o''brien'");
  assert.equal(sqlLiteral(0), "0");
  assert.equal(sqlLiteral(""), "''");
});

test("insertStatement composes columns and escaped values", () => {
  const stmt = insertStatement("relay_client_sessions", ["session_id", "revoked_at"], ["s'1", ""]);
  assert.equal(stmt, "INSERT INTO relay_client_sessions (session_id, revoked_at) VALUES ('s''1', '');");
});

test("relay db name is the renamed (non-spike) target", () => {
  assert.equal(RELAY_DB_NAME, "torque-relay");
});

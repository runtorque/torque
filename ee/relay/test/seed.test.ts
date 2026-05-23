import assert from "node:assert/strict";
import test from "node:test";

import { hashSecret } from "../src/core/auth.js";
import {
  RELAY_DB_NAME,
  buildClientEstablishCodeSeed,
  buildDaemonCredentialSeed,
  generateDaemonKeyMaterial,
  insertStatement,
  sqlLiteral,
} from "../src/tools/seed.js";

test("establish-code seed hash EXACTLY matches the relay's hashSecret", async () => {
  // hashSecret trims then SHA-256; the seeded code_hash must be byte-identical to
  // what /establish computes from the code at redeem time, or it never matches.
  const seed = await buildClientEstablishCodeSeed({ ownerUserId: "owner-1", rawCode: "  spaced-code  " });
  assert.equal(seed.record.code_hash, await hashSecret("  spaced-code  "));
});

test("establish-code seed never persists the raw code in the emitted SQL", async () => {
  const seed = await buildClientEstablishCodeSeed({ ownerUserId: "owner-1", daemonId: "daemon-1" });
  assert.ok(seed.rawCode.length >= 16, "generated code should have real entropy");
  assert.ok(!seed.statement.includes(seed.rawCode), "raw code must not appear in SQL");
  assert.ok(seed.statement.includes(seed.record.code_hash), "SQL must carry the hash");
  assert.equal(seed.record.code_hash, await hashSecret(seed.rawCode));
});

test("establish-code seed is unconsumed and carries owner + daemon scope", async () => {
  const seed = await buildClientEstablishCodeSeed({ ownerUserId: "owner-1", daemonId: "daemon-1" });
  assert.equal(seed.record.owner_user_id, "owner-1");
  assert.equal(seed.record.daemon_id, "daemon-1");
  assert.equal(seed.record.consumed_at, "");
  assert.equal(seed.record.revoked_at, "");
  assert.ok(seed.record.expires_at > seed.record.created_at);
});

test("generated establish codes are unique", async () => {
  const a = await buildClientEstablishCodeSeed({ ownerUserId: "owner-1" });
  const b = await buildClientEstablishCodeSeed({ ownerUserId: "owner-1" });
  assert.notEqual(a.rawCode, b.rawCode);
  assert.notEqual(a.record.code_hash, b.record.code_hash);
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
  const stmt = insertStatement("relay_client_establish_codes", ["id", "consumed_at"], ["c'1", ""]);
  assert.equal(stmt, "INSERT INTO relay_client_establish_codes (id, consumed_at) VALUES ('c''1', '');");
});

test("relay db name is the renamed (non-spike) target", () => {
  assert.equal(RELAY_DB_NAME, "torque-relay");
});

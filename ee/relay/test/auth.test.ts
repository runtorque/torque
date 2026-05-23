import assert from "node:assert/strict";
import test from "node:test";

import {
  authenticateDaemonAttach,
  canonicalDaemonAttachString,
  hashSecret,
  parseDaemonAttachAuthorization,
  selectStandaloneAuthMode,
} from "../src/core/auth.js";
import { SqliteRelayStore } from "../src/adapters/standalone/sqliteStore.js";
import { createDaemonCredentialFixture, signedDaemonAttachHeader } from "./helpers/auth.js";

class TestHeaders {
  constructor(private readonly values: Record<string, string>) {}
  get(name: string): string | null {
    return this.values[name.toLowerCase()] || null;
  }
}

test("daemon signed attach authenticates, rejects wrong signature, and rejects replayed nonce", async () => {
  const store = new SqliteRelayStore(":memory:");
  await store.migrate();
  const fixture = await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
  const url = new URL("https://relay.example.com/v1/daemon/daemon-1/ws");
  const now = new Date("2026-05-23T00:00:00.000Z");
  const header = await signedDaemonAttachHeader(fixture, { timestamp: now.toISOString(), nonce: "nonce-1" });
  const principal = await authenticateDaemonAttach(store, {
    method: "GET",
    url,
    daemonId: "daemon-1",
    headers: new TestHeaders({ authorization: header }),
  }, { now });
  assert.equal(principal.ownerUserId, "owner-1");
  assert.equal(principal.credentialId, "cred-1");

  await assert.rejects(
    () => authenticateDaemonAttach(store, { method: "GET", url, daemonId: "daemon-1", headers: new TestHeaders({ authorization: header }) }, { now }),
    /nonce has already been used/,
  );

  const bad = (await signedDaemonAttachHeader(fixture, { timestamp: now.toISOString(), nonce: "nonce-2" })).replace(/signature="[^"]+"/, 'signature="bogus"');
  await assert.rejects(
    () => authenticateDaemonAttach(store, { method: "GET", url, daemonId: "daemon-1", headers: new TestHeaders({ authorization: bad }) }, { now }),
    /signature is invalid|Invalid character/,
  );
  await store.close();
});

test("auth helpers keep canonical request and fail-closed non-loopback policy stable", async () => {
  assert.equal(selectStandaloneAuthMode({ host: "127.0.0.1" }), "local-dev-unauthenticated");
  assert.equal(selectStandaloneAuthMode({ host: "0.0.0.0" }), "required");
  assert.throws(() => selectStandaloneAuthMode({ host: "0.0.0.0", requestedMode: "local-dev-unauthenticated" }), /non-loopback/);
  const canonical = canonicalDaemonAttachString({
    method: "get",
    path: "/v1/daemon/daemon-1/ws",
    daemonId: "daemon-1",
    credentialId: "cred-1",
    timestamp: "2026-05-23T00:00:00.000Z",
    nonce: "nonce-1",
  });
  assert.match(canonical, /^torque-daemon-attach-v1\nGET\n\/v1\/daemon\/daemon-1\/ws\ndaemon_id:daemon-1/m);
  const parsed = parseDaemonAttachAuthorization('Torque-Daemon-Signature v1 credential_id="cred-1", timestamp="t", nonce="n", signature="s"');
  assert.equal(parsed.credentialId, "cred-1");
  assert.equal((await hashSecret("secret")).length, 64);
});

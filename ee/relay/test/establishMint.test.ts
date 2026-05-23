import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_ESTABLISH_CODE_TTL_MS,
  mintClientEstablishCode,
  type MintCoordinatorLike,
  type MintEstablishCodeArgs,
} from "../src/core/establishMint.js";
import { RelayAuthError, hashSecret } from "../src/core/auth.js";
import type { DaemonAuthPrincipal, RelayStore } from "../src/core/ports.js";
import { SqliteRelayStore } from "../src/adapters/standalone/sqliteStore.js";
import { D1RelayStore } from "../src/adapters/cloudflare/d1Store.js";
import { FakeD1Database } from "./helpers/fakeD1.js";
import { createDaemonCredentialFixture } from "./helpers/auth.js";

const ALWAYS_CURRENT: MintCoordinatorLike = { isCurrentDaemonConnection: () => true };

function daemonPrincipal(over: Partial<DaemonAuthPrincipal> = {}): DaemonAuthPrincipal {
  return {
    kind: "daemon",
    daemonId: "daemon-1",
    ownerUserId: "owner-1",
    credentialId: "cred-1",
    authMode: "signed-attach-v1",
    ...over,
  };
}

function mintArgs(store: RelayStore, over: Partial<MintEstablishCodeArgs> = {}): MintEstablishCodeArgs {
  return {
    store,
    coordinator: ALWAYS_CURRENT,
    daemonId: "daemon-1",
    principal: daemonPrincipal(),
    connectionId: "daemon-conn-1",
    epoch: 0,
    payload: { nonce: `nonce-${crypto.randomUUID()}` },
    options: { now: new Date("2026-05-23T00:00:00.000Z") },
    ...over,
  };
}

// Run the identical security battery against BOTH storage adapters so the
// cloudflare DO/worker path and the standalone path are both covered.
type StoreFactory = { name: string; create(): Promise<{ store: RelayStore; close(): Promise<void> }> };

const STORE_FACTORIES: StoreFactory[] = [
  {
    name: "sqlite (standalone)",
    async create() {
      const store = new SqliteRelayStore(":memory:");
      await store.migrate();
      return { store, close: () => store.close() };
    },
  },
  {
    name: "d1 (cloudflare)",
    async create() {
      const db = new FakeD1Database();
      const store = new D1RelayStore(db as unknown as D1Database);
      await store.migrate();
      return { store, close: async () => db.close() };
    },
  },
];

for (const factory of STORE_FACTORIES) {
  test(`[${factory.name}] mints owner-scoped code, persists hash only, returns raw once`, async () => {
    const { store, close } = await factory.create();
    await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });

    const now = new Date("2026-05-23T00:00:00.000Z");
    const result = await mintClientEstablishCode(mintArgs(store, {
      // A payload-supplied owner MUST be ignored: the owner comes from the attach.
      payload: { nonce: "nonce-a", owner_user_id: "attacker", label: "iphone" },
      options: { now },
    }));

    // [1] owner is server-side from the attach principal, never the payload.
    assert.equal(result.ownerUserId, "owner-1");
    assert.equal(result.expiresAt, new Date(now.getTime() + DEFAULT_ESTABLISH_CODE_TTL_MS).toISOString());
    assert.ok(result.code.length >= 32);

    // [2] only the hash is persisted: consuming by the HASH matches, consuming by
    // the RAW code matches nothing (proving raw was never stored). Use an explicit
    // consume time inside the validity window so the assertion is wall-clock-proof.
    const within = new Date(now.getTime() + 1000).toISOString();
    const byRaw = await store.consumeClientEstablishCode(result.code, within);
    assert.equal(byRaw, null);
    const byHash = await store.consumeClientEstablishCode(await hashSecret(result.code), within);
    assert.ok(byHash);
    assert.equal(byHash?.owner_user_id, "owner-1");
    assert.notEqual(byHash?.code_hash, result.code);
    await close();
  });

  test(`[${factory.name}] single-use: a minted code redeems exactly once`, async () => {
    const { store, close } = await factory.create();
    await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
    const now = new Date("2026-05-23T00:00:00.000Z");
    const result = await mintClientEstablishCode(mintArgs(store, { payload: { nonce: "nonce-single" }, options: { now } }));
    const within = new Date(now.getTime() + 1000).toISOString();
    const first = await store.consumeClientEstablishCode(await hashSecret(result.code), within);
    assert.ok(first);
    const second = await store.consumeClientEstablishCode(await hashSecret(result.code), within);
    assert.equal(second, null);
    await close();
  });

  test(`[${factory.name}] replay-rejected: a reused nonce cannot mint twice`, async () => {
    const { store, close } = await factory.create();
    await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
    await mintClientEstablishCode(mintArgs(store, { payload: { nonce: "replayed" } }));
    await assert.rejects(
      () => mintClientEstablishCode(mintArgs(store, { payload: { nonce: "replayed" } })),
      /nonce has already been used/,
    );
    await close();
  });

  test(`[${factory.name}] owner-scoping: a principal owner mismatch is rejected`, async () => {
    const { store, close } = await factory.create();
    await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
    await assert.rejects(
      () => mintClientEstablishCode(mintArgs(store, { principal: daemonPrincipal({ ownerUserId: "owner-2" }) })),
      /owner does not match/,
    );
    await close();
  });

  test(`[${factory.name}] fencing: stale connection, stale epoch, and rotated credential are rejected`, async () => {
    const { store, close } = await factory.create();
    await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });

    // (a) connection-level fencing — a stale/replaced socket cannot mint (P5).
    await assert.rejects(
      () => mintClientEstablishCode(mintArgs(store, {
        coordinator: { isCurrentDaemonConnection: () => false },
        payload: { nonce: "fence-conn" },
      })),
      /stale or fenced/,
    );

    // (b) persisted fencing-epoch monotonicity — a newer owner connection claimed
    // a higher epoch, so this stale epoch is superseded (P5).
    await store.claimInstanceOwner({ id: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1", fencingEpoch: 5 });
    await assert.rejects(
      () => mintClientEstablishCode(mintArgs(store, { epoch: 3, payload: { nonce: "fence-epoch" } })),
      /fencing epoch is stale/,
    );

    // (c) rotated credential — the attach credential is no longer the active one.
    await assert.rejects(
      () => mintClientEstablishCode(mintArgs(store, {
        epoch: 5,
        principal: daemonPrincipal({ credentialId: "cred-rotated" }),
        payload: { nonce: "fence-rotated" },
      })),
      /credential/,
    );
    await close();
  });

  test(`[${factory.name}] revoked credential is rejected`, async () => {
    const { store, close } = await factory.create();
    await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
    await store.revokeDaemonCredential("cred-1");
    await assert.rejects(
      () => mintClientEstablishCode(mintArgs(store, { payload: { nonce: "revoked" } })),
      /not active/,
    );
    await close();
  });

  test(`[${factory.name}] rate-limited: per-daemon mint cap is enforced`, async () => {
    const { store, close } = await factory.create();
    await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
    const now = new Date("2026-05-23T00:00:00.000Z");
    const opts = { now, rateLimit: 2, rateWindowMs: 10 * 60 * 1000 };
    await mintClientEstablishCode(mintArgs(store, { payload: { nonce: "rl-1" }, options: opts }));
    await mintClientEstablishCode(mintArgs(store, { payload: { nonce: "rl-2" }, options: opts }));
    await assert.rejects(
      () => mintClientEstablishCode(mintArgs(store, { payload: { nonce: "rl-3" }, options: opts })),
      /rate limit exceeded/,
    );
    await close();
  });

  test(`[${factory.name}] missing nonce is rejected before any mint`, async () => {
    const { store, close } = await factory.create();
    await createDaemonCredentialFixture(store, { daemonId: "daemon-1", ownerUserId: "owner-1", credentialId: "cred-1" });
    await assert.rejects(
      () => mintClientEstablishCode(mintArgs(store, { payload: {} })),
      /nonce is required/,
    );
    assert.equal(await store.countRecentEstablishCodes("daemon-1", "2000-01-01T00:00:00.000Z"), 0);
    await close();
  });
}

// TOCTOU race regression (TORQUE:611 fix): on the DO path the D1 awaits between
// the initial connection-fencing check and the mint are UNGATED (D1 is an
// external binding, not DO transactional storage), so a superseding same-owner
// attach can land mid-flight. The FINAL isCurrentDaemonConnection re-check placed
// immediately before createClientEstablishCode closes that window. This test
// genuinely exercises the window: isCurrentDaemonConnection returns true on the
// first (initial) check and false on the second (final re-check). It FAILS on
// pre-fix code — without the re-check the mint proceeds and createClientEstablishCode
// is called — and PASSES with the re-check (verified by temporarily reverting it).
test("race: a superseding attach during the ungated D1 window fences the mint after the nonce burn", async () => {
  const calls = { recordAuthNonce: 0, createClientEstablishCode: 0 };
  let isCurrentCalls = 0;
  const coordinator: MintCoordinatorLike = {
    isCurrentDaemonConnection: () => {
      isCurrentCalls += 1;
      // 1st call = the initial top-of-function check (passes); 2nd call = the new
      // final re-check, where a superseding same-owner attach has now landed.
      return isCurrentCalls === 1;
    },
  };
  const store = {
    async getInstance() {
      return {
        id: "daemon-1", owner_user_id: "owner-1", label: "daemon-1",
        created_at: "", last_seen_at: "", fencing_epoch: 0,
        active_credential_id: "cred-1", coordination_updated_at: "", metadata: {},
      };
    },
    async getDaemonCredential() {
      return {
        credential_id: "cred-1", daemon_id: "daemon-1", owner_user_id: "owner-1",
        public_key_jwk: {}, alg: "ES256", created_at: "", last_used_at: "",
        revoked_at: "", metadata: {},
      };
    },
    async countRecentEstablishCodes() {
      return 0;
    },
    async recordAuthNonce() {
      calls.recordAuthNonce += 1;
      return true;
    },
    async createClientEstablishCode(record: unknown) {
      calls.createClientEstablishCode += 1;
      return record;
    },
  } as unknown as RelayStore;

  await assert.rejects(
    () => mintClientEstablishCode({
      store,
      coordinator,
      daemonId: "daemon-1",
      principal: {
        kind: "daemon", daemonId: "daemon-1", ownerUserId: "owner-1",
        credentialId: "cred-1", authMode: "signed-attach-v1",
      },
      connectionId: "daemon-conn-1",
      epoch: 1,
      payload: { nonce: "race-nonce" },
      options: { now: new Date("2026-05-23T00:00:00.000Z") },
    }),
    (err: unknown) => err instanceof RelayAuthError && err.code === "stale_daemon_connection",
  );

  // The nonce was burned BEFORE the fence — replay protection stays intact (a
  // same-nonce retry would also fail) ...
  assert.equal(calls.recordAuthNonce, 1);
  // ... and crucially NO code was minted.
  assert.equal(calls.createClientEstablishCode, 0);
  // Both the initial check and the final re-check ran (proving the window is exercised).
  assert.equal(isCurrentCalls, 2);
});

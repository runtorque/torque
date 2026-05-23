import assert from "node:assert/strict";
import test from "node:test";

import worker from "../src/adapters/cloudflare/worker.js";
import { D1RelayStore } from "../src/adapters/cloudflare/d1Store.js";
import { hashSecret } from "../src/core/auth.js";
import { FakeD1Database } from "./helpers/fakeD1.js";

class FakeNamespace {
  idFromName(name: string): string { return name; }
  get(_id: string): { fetch(request: Request): Promise<Response> } {
    return { fetch: async () => new Response(JSON.stringify({ type: "ok" }), { headers: { "content-type": "application/json" } }) };
  }
}

const INDEX_HTML = '<!doctype html><html><head>'
  + '<script id="torque-remote-config" type="application/json">{}</script>'
  + '</head><body>app</body></html>';

// Echoes the resolved asset path, except /index.html which returns the bundle
// HTML carrying the empty config tag (so injection can be asserted).
const fakeAssets = {
  fetch: async (request: Request): Promise<Response> => {
    const path = new URL(request.url).pathname;
    if (path === "/index.html") {
      return new Response(INDEX_HTML, { status: 200, headers: { "content-type": "text/html" } });
    }
    return new Response(`asset:${path}`, { status: 200, headers: { "content-type": "text/plain" } });
  },
};

function env(db: FakeD1Database, opts: { assets?: boolean; daemonId?: string } = {}) {
  return {
    RELAY_DB: db as unknown as D1Database,
    RENDEZVOUS: new FakeNamespace() as any,
    ASSETS: opts.assets === false ? undefined : (fakeAssets as any),
    RELAY_DAEMON_ID: opts.daemonId,
  };
}

async function seededStore(): Promise<{ db: FakeD1Database; store: D1RelayStore }> {
  const db = new FakeD1Database();
  const store = new D1RelayStore(db as unknown as D1Database);
  await store.migrate();
  return { db, store };
}

async function seedCode(
  store: D1RelayStore,
  opts: { owner?: string; daemonId?: string; code?: string; expiresAt?: string } = {},
): Promise<string> {
  const rawCode = opts.code || `code-${crypto.randomUUID()}`;
  await store.createClientEstablishCode({
    id: `establish-${crypto.randomUUID()}`,
    code_hash: await hashSecret(rawCode),
    owner_user_id: opts.owner || "owner-1",
    daemon_id: opts.daemonId || "",
    label: "",
    created_at: "2026-05-23T00:00:00.000Z",
    expires_at: opts.expiresAt || "2099-01-01T00:00:00.000Z",
    consumed_at: "",
    revoked_at: "",
    metadata: {},
  });
  return rawCode;
}

function cookieToken(setCookie: string): string {
  const match = /torque_session=([^;]+)/.exec(setCookie || "");
  return match ? decodeURIComponent(match[1]) : "";
}

test("GET /establish redeems a valid code, mints a session, sets the HttpOnly cookie, redirects to /app/", async () => {
  const { db, store } = await seededStore();
  const code = await seedCode(store, { owner: "owner-1" });
  const response = await worker.fetch(
    new Request(`https://relay.runtorque.com/establish?code=${encodeURIComponent(code)}`),
    env(db),
  );
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("location"), "/app/");
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
  const cookie = response.headers.get("set-cookie") || "";
  assert.match(cookie, /^torque_session=/);
  assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /Secure/);
  assert.match(cookie, /SameSite=Lax/);
  assert.match(cookie, /Path=\//);
  // The minted token is fresh and resolves to an active session by its hash.
  const token = cookieToken(cookie);
  assert.ok(token.length >= 16);
  assert.ok(!token.includes(code), "minted session token must not be the establish code");
  const session = await store.getClientSessionByTokenHash(await hashSecret(token));
  assert.equal(session?.owner_user_id, "owner-1");
  db.close();
});

test("GET /establish is single-use: a second redemption of the same code fails into reauth_required", async () => {
  const { db, store } = await seededStore();
  const code = await seedCode(store, { owner: "owner-1" });
  const first = await worker.fetch(new Request(`https://relay.runtorque.com/establish?code=${encodeURIComponent(code)}`), env(db));
  assert.equal(first.status, 302);
  const second = await worker.fetch(new Request(`https://relay.runtorque.com/establish?code=${encodeURIComponent(code)}`), env(db));
  assert.equal(second.status, 401);
  const body = await second.json() as { code?: string };
  assert.equal(body.code, "reauth_required");
  assert.equal(second.headers.get("set-cookie"), null);
  db.close();
});

test("GET /establish with an unknown code → 401 reauth_required, no cookie, no session minted", async () => {
  const { db, store } = await seededStore();
  const response = await worker.fetch(new Request("https://relay.runtorque.com/establish?code=nope"), env(db));
  assert.equal(response.status, 401);
  assert.equal((await response.json() as { code?: string }).code, "reauth_required");
  assert.equal(response.headers.get("set-cookie"), null);
  assert.equal(await store.getClientSessionByTokenHash(await hashSecret("nope")), null);
  db.close();
});

test("GET /establish with an expired code → 401 reauth_required", async () => {
  const { db, store } = await seededStore();
  const code = await seedCode(store, { expiresAt: "2000-01-01T00:00:00.000Z" });
  const response = await worker.fetch(new Request(`https://relay.runtorque.com/establish?code=${encodeURIComponent(code)}`), env(db));
  assert.equal(response.status, 401);
  assert.equal(response.headers.get("set-cookie"), null);
  db.close();
});

test("GET /establish with no code → 401 reauth_required", async () => {
  const { db } = await seededStore();
  const response = await worker.fetch(new Request("https://relay.runtorque.com/establish"), env(db));
  assert.equal(response.status, 401);
  db.close();
});

test("GET /establish rejects an owner-mismatched code against the configured daemon instance", async () => {
  const { db, store } = await seededStore();
  await store.upsertInstance({
    id: "daemon-1",
    owner_user_id: "owner-A",
    label: "daemon-1",
    created_at: "2026-05-23T00:00:00.000Z",
    last_seen_at: "2026-05-23T00:00:00.000Z",
    fencing_epoch: 0,
    active_credential_id: "",
    coordination_updated_at: "2026-05-23T00:00:00.000Z",
    metadata: {},
  });
  const code = await seedCode(store, { owner: "owner-B" });
  const response = await worker.fetch(
    new Request(`https://relay.runtorque.com/establish?code=${encodeURIComponent(code)}`),
    env(db, { daemonId: "daemon-1" }),
  );
  assert.equal(response.status, 401);
  assert.equal(response.headers.get("set-cookie"), null);
  db.close();
});

test("GET /app redirects to /app/ so relative bundle asset URLs resolve", async () => {
  const { db } = await seededStore();
  const response = await worker.fetch(new Request("https://relay.runtorque.com/app"), env(db));
  assert.equal(response.status, 308);
  assert.equal(response.headers.get("location"), "/app/");
  db.close();
});

test("GET /app/ serves index.html with the injected same-origin RemoteConfig + Referrer-Policy", async () => {
  const { db } = await seededStore();
  const response = await worker.fetch(new Request("https://relay.runtorque.com/app/"), env(db, { daemonId: "daemon-1" }));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
  const html = await response.text();
  assert.ok(html.includes('"relayBaseUrl":"https://relay.runtorque.com"'), "relayBaseUrl = request origin");
  assert.ok(html.includes('"daemonId":"daemon-1"'));
  assert.ok(html.includes('"authMode":"cookie"'));
  assert.ok(!html.includes(">{}</script>"), "empty config tag must be replaced");
  db.close();
});

test("GET /app/js/app.js maps to the asset path with the /app prefix stripped (+ Referrer-Policy)", async () => {
  const { db } = await seededStore();
  const response = await worker.fetch(new Request("https://relay.runtorque.com/app/js/app.js"), env(db));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
  assert.equal(await response.text(), "asset:/js/app.js");
  db.close();
});

test("GET /app/ returns 500 when no assets binding is configured", async () => {
  const { db } = await seededStore();
  const response = await worker.fetch(new Request("https://relay.runtorque.com/app/"), env(db, { assets: false }));
  assert.equal(response.status, 500);
  db.close();
});

test("the establish/static routes never shadow the API routes (/health still required-mode JSON)", async () => {
  const { db } = await seededStore();
  const response = await worker.fetch(new Request("https://relay.runtorque.com/health"), env(db));
  assert.equal(response.status, 200);
  assert.equal((await response.json() as { auth_mode?: string }).auth_mode, "required");
  db.close();
});

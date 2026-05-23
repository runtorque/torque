import assert from "node:assert/strict";
import test from "node:test";

import worker from "../src/adapters/cloudflare/worker.js";
import { D1RelayStore } from "../src/adapters/cloudflare/d1Store.js";
import { FakeD1Database } from "./helpers/fakeD1.js";
import { createClientSessionFixture } from "./helpers/auth.js";

class FakeNamespace {
  idFromName(name: string): string { return name; }
  get(_id: string): { fetch(request: Request): Promise<Response> } {
    return { fetch: async () => new Response(JSON.stringify({ type: "ok" }), { headers: { "content-type": "application/json" } }) };
  }
}

// Echoes the resolved asset path so tests can assert the /app → asset rewrite.
const fakeAssets = {
  fetch: async (request: Request): Promise<Response> => {
    const path = new URL(request.url).pathname;
    return new Response(`asset:${path}`, { status: 200, headers: { "content-type": "text/plain" } });
  },
};

function env(db: FakeD1Database, withAssets = true) {
  return {
    RELAY_DB: db as unknown as D1Database,
    RENDEZVOUS: new FakeNamespace() as any,
    ASSETS: withAssets ? (fakeAssets as any) : undefined,
  };
}

async function seededStore(): Promise<{ db: FakeD1Database; store: D1RelayStore }> {
  const db = new FakeD1Database();
  const store = new D1RelayStore(db as unknown as D1Database);
  await store.migrate();
  return { db, store };
}

test("POST /session with a valid token sets the HttpOnly login cookie and redirects to /app/", async () => {
  const { db, store } = await seededStore();
  const fixture = await createClientSessionFixture(store, { ownerUserId: "owner-1" });
  const response = await worker.fetch(
    new Request("https://relay.runtorque.com/session", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: fixture.token }),
    }),
    env(db),
  );
  assert.equal(response.status, 303);
  assert.equal(response.headers.get("location"), "/app/");
  const cookie = response.headers.get("set-cookie") || "";
  assert.match(cookie, /^torque_session=/);
  assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /Secure/);
  assert.match(cookie, /SameSite=Lax/);
  assert.match(cookie, /Path=\//);
  // The cookie must carry the raw token so the WS upgrade can re-derive the hash.
  assert.ok(cookie.includes(encodeURIComponent(fixture.token)));
  db.close();
});

test("POST /session accepts a form-encoded body (plain same-origin HTML form)", async () => {
  const { db, store } = await seededStore();
  const fixture = await createClientSessionFixture(store, { ownerUserId: "owner-1" });
  const response = await worker.fetch(
    new Request("https://relay.runtorque.com/session", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: `token=${encodeURIComponent(fixture.token)}`,
    }),
    env(db),
  );
  assert.equal(response.status, 303);
  assert.match(response.headers.get("set-cookie") || "", /^torque_session=/);
  db.close();
});

test("POST /session rejects an unknown token with 401 and no cookie", async () => {
  const { db } = await seededStore();
  const response = await worker.fetch(
    new Request("https://relay.runtorque.com/session", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: "not-a-real-token" }),
    }),
    env(db),
  );
  assert.equal(response.status, 401);
  assert.equal(response.headers.get("set-cookie"), null);
  db.close();
});

test("POST /session rejects a revoked session with 401 and no cookie", async () => {
  const { db, store } = await seededStore();
  const fixture = await createClientSessionFixture(store, { ownerUserId: "owner-1" });
  await store.revokeClientSession(fixture.sessionId);
  const response = await worker.fetch(
    new Request("https://relay.runtorque.com/session", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: fixture.token }),
    }),
    env(db),
  );
  assert.equal(response.status, 401);
  assert.equal(response.headers.get("set-cookie"), null);
  db.close();
});

test("POST /session rejects an expired session with 401", async () => {
  const { db, store } = await seededStore();
  const fixture = await createClientSessionFixture(store, {
    ownerUserId: "owner-1",
    expiresAt: "2000-01-01T00:00:00.000Z",
  });
  const response = await worker.fetch(
    new Request("https://relay.runtorque.com/session", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: fixture.token }),
    }),
    env(db),
  );
  assert.equal(response.status, 401);
  db.close();
});

test("POST /session with no token is a 400, not a 401", async () => {
  const { db } = await seededStore();
  const response = await worker.fetch(
    new Request("https://relay.runtorque.com/session", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({}),
    }),
    env(db),
  );
  assert.equal(response.status, 400);
  db.close();
});

test("GET /app redirects to /app/ so relative bundle asset URLs resolve", async () => {
  const { db } = await seededStore();
  const response = await worker.fetch(new Request("https://relay.runtorque.com/app"), env(db));
  assert.equal(response.status, 308);
  assert.equal(response.headers.get("location"), "/app/");
  db.close();
});

test("GET /app/ serves the bundle index.html from the assets binding", async () => {
  const { db } = await seededStore();
  const response = await worker.fetch(new Request("https://relay.runtorque.com/app/"), env(db));
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "asset:/index.html");
  db.close();
});

test("GET /app/js/app.js maps to the asset path with the /app prefix stripped", async () => {
  const { db } = await seededStore();
  const response = await worker.fetch(new Request("https://relay.runtorque.com/app/js/app.js"), env(db));
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "asset:/js/app.js");
  db.close();
});

test("GET /app/ returns 500 when no assets binding is configured", async () => {
  const { db } = await seededStore();
  const response = await worker.fetch(new Request("https://relay.runtorque.com/app/"), env(db, false));
  assert.equal(response.status, 500);
  db.close();
});

test("the static-serve path never shadows the API routes (/health still required-mode JSON)", async () => {
  const { db } = await seededStore();
  const response = await worker.fetch(new Request("https://relay.runtorque.com/health"), env(db));
  assert.equal(response.status, 200);
  const body = await response.json() as { auth_mode?: string };
  assert.equal(body.auth_mode, "required");
  db.close();
});

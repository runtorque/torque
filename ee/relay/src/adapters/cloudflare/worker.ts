import { parseRelayEnvelope } from "../../core/protocol.js";
import { errorMessage } from "../../core/errors.js";
import {
  authErrorStatus,
  authenticateClientSession,
  hashSecret,
  sanitizeClientEnvelopeForV1,
} from "../../core/auth.js";
import { nowIso } from "../../core/sql.js";
import { D1RelayStore } from "./d1Store.js";
export { DaemonRendezvousDurableObject } from "./durableObjectCoordinator.js";

// Same-origin remote UI mount + login cookie (Channels go-live §2b, same-origin
// topology decision-f1f446b96707): the Worker static-serves the remote bundle
// under /app/ and trades a single-use establish code for a freshly minted client
// session set as the HttpOnly torque_session cookie. Same-origin ⇒ SameSite=Lax +
// no CORS, which sidesteps the third-party-cookie phaseout that would otherwise
// break a cross-site cookie WS upgrade. The long-lived session token is born
// server-side here and lives ONLY in the HttpOnly cookie — never in any URL/log.
const APP_PREFIX = "/app";
const ESTABLISH_PATH = "/establish";
const SESSION_COOKIE = "torque_session";
const NO_REFERRER = "no-referrer";
// Minted session lifetime; the establish code itself is separately short-lived.
const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const CONFIG_TAG_RE = /(<script id="torque-remote-config"[^>]*>)([\s\S]*?)(<\/script>)/;

export interface Env {
  RELAY_DB: D1Database;
  RENDEZVOUS: DurableObjectNamespace;
  // Static assets binding for the remote bundle (ee/frontend/remote). Optional so
  // non-asset deployments/tests that never hit /app still type-check.
  ASSETS?: Fetcher;
  // Non-secret wrangler [vars]: the paired daemon_id injected into the served
  // RemoteConfig, and an optional public-origin override for relayBaseUrl
  // (defaults to the request origin, i.e. https://relay.runtorque.com).
  RELAY_DAEMON_ID?: string;
  RELAY_PUBLIC_ORIGIN?: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      return await handleRequest(request, env);
    } catch (error) {
      return json({ type: "error", message: errorMessage(error) }, 500);
    }
  },
};

async function handleRequest(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const store = new D1RelayStore(env.RELAY_DB);
  if (request.method === "GET" && url.pathname === "/health") {
    // Schema migrations are applied out-of-band only, via
    // `wrangler d1 migrations apply`. The relay deliberately exposes no public
    // unauthenticated DDL hatch on a reachable deployment.
    return json({
      type: "ok",
      relay: "torque-ee-relay",
      storage: "d1",
      coordination: "durable-object",
      protocol_version: 1,
      auth_mode: "required",
    });
  }

  // Same-origin establish: redeem a single-use code (?code=) for a freshly minted
  // client session, set as the HttpOnly cookie. The code may ride the URL because
  // it is single-use, short-lived, and consumed-on-use (inert if leaked); the
  // long-lived session token never does. Referrer-Policy: no-referrer prevents the
  // code leaking via Referer to /app/ subresources.
  if (request.method === "GET" && url.pathname === ESTABLISH_PATH) {
    return handleEstablish(request, env, store, url);
  }

  // Static-serve the remote bundle under /app/. run_worker_first keeps the Worker
  // in front of every request; only /app* is delegated to the assets binding.
  if ((request.method === "GET" || request.method === "HEAD") && isAppRoute(url.pathname)) {
    return handleAppAsset(request, env, url);
  }

  const wsRoute = url.pathname.match(/^\/v1\/(?:daemon|client)\/([^/]+)\/ws$/);
  if (wsRoute && request.headers.get("upgrade")?.toLowerCase() === "websocket") {
    const daemonId = decodeURIComponent(wsRoute[1]);
    const id = env.RENDEZVOUS.idFromName(daemonId);
    return env.RENDEZVOUS.get(id).fetch(request);
  }

  const postMessage = url.pathname.match(/^\/v1\/messages\/([^/]+)$/);
  if (request.method === "POST" && postMessage) {
    const daemonId = decodeURIComponent(postMessage[1]);
    let principal;
    try {
      principal = await authenticateClientSession(store, { daemonId, headers: request.headers });
    } catch (error) {
      return json({ type: "error", message: errorMessage(error), code: (error as { code?: string }).code || "relay_auth_error" }, authErrorStatus(error));
    }
    const envelope = sanitizeClientEnvelopeForV1(parseRelayEnvelope(await request.json()), principal);
    if (envelope.daemon_id !== daemonId) {
      return json({ type: "error", message: "daemon_id path/envelope mismatch" }, 400);
    }
    const saved = await store.appendMessage(envelope, "to_daemon");
    const id = env.RENDEZVOUS.idFromName(daemonId);
    const routed = await env.RENDEZVOUS.get(id).fetch(
      new Request(new URL(`/internal/route-to-daemon/${encodeURIComponent(daemonId)}`, url.origin), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(envelope),
      }),
    );
    const routeResult = await routed.json() as { delivery?: { delivered?: boolean; epoch?: number } };
    if (routeResult.delivery?.delivered) {
      await store.markDeliveryAttempt(envelope.id, routeResult.delivery.epoch || 0);
    }
    return json({ type: "ok", message: saved, delivery: routeResult.delivery || null }, 202);
  }

  return json({ type: "error", message: "not found" }, 404);
}

async function handleEstablish(request: Request, env: Env, store: D1RelayStore, url: URL): Promise<Response> {
  const code = String(url.searchParams.get("code") || "").trim();
  if (!code) return reauthRequired("establish code is required");
  // Atomic single-use redemption: a reused/expired/unknown code returns null and
  // mints nothing (no race, no double-mint). Hashed lookup — never a raw compare.
  const redeemed = await store.consumeClientEstablishCode(await hashSecret(code));
  if (!redeemed) return reauthRequired("establish code is invalid, expired, or already used");
  // Defensive owner check against the configured daemon instance, when present.
  const daemonId = String(env.RELAY_DAEMON_ID || redeemed.daemon_id || "").trim();
  if (daemonId) {
    const instance = await store.getInstance(daemonId);
    if (instance?.owner_user_id && instance.owner_user_id !== redeemed.owner_user_id) {
      return reauthRequired("establish code owner does not match the daemon owner");
    }
  }
  // Mint the long-lived session server-side. Only its hash is stored; the raw
  // token exists transiently here and in the HttpOnly cookie response only.
  const rawToken = randomToken();
  const now = nowIso();
  await store.createClientSession({
    session_id: `session-${crypto.randomUUID()}`,
    token_hash: await hashSecret(rawToken),
    owner_user_id: redeemed.owner_user_id,
    created_at: now,
    expires_at: new Date(Date.parse(now) + SESSION_TTL_MS).toISOString(),
    revoked_at: "",
    metadata: { established_from: "establish-code", code_id: redeemed.id },
  });
  // HttpOnly: JS can never read the token. SameSite=Lax suffices same-origin and
  // avoids the third-party-cookie phaseout. Secure ⇒ https/wss only. Path=/ so the
  // cookie rides the /v1/client WS upgrade.
  const cookie = `${SESSION_COOKIE}=${encodeURIComponent(rawToken)}; HttpOnly; Secure; SameSite=Lax; Path=/`;
  return new Response(null, {
    status: 302,
    headers: { "set-cookie": cookie, location: `${APP_PREFIX}/`, "referrer-policy": NO_REFERRER },
  });
}

// 401 in the shape the remote landing treats as reauth_required ("re-pair, scan
// again") — never a silent success or a second session. No cookie is set.
function reauthRequired(message: string): Response {
  return new Response(JSON.stringify({ type: "error", code: "reauth_required", message }), {
    status: 401,
    headers: { "content-type": "application/json; charset=utf-8", "referrer-policy": NO_REFERRER },
  });
}

function isAppRoute(pathname: string): boolean {
  return pathname === APP_PREFIX || pathname.startsWith(`${APP_PREFIX}/`);
}

async function handleAppAsset(request: Request, env: Env, url: URL): Promise<Response> {
  if (!env.ASSETS) {
    return json({ type: "error", message: "static assets are not configured" }, 500);
  }
  // The bundle's index.html references assets RELATIVELY (css/…, js/…), so it only
  // resolves under a trailing slash. Redirect bare /app → /app/.
  if (url.pathname === APP_PREFIX) {
    return new Response(null, { status: 308, headers: { location: `${APP_PREFIX}/`, "referrer-policy": NO_REFERRER } });
  }
  let assetPath = url.pathname.slice(APP_PREFIX.length); // "/" or "/js/app.js"
  const isIndex = assetPath === "/" || assetPath === "/index.html";
  if (assetPath === "/") assetPath = "/index.html";
  const assetUrl = new URL(assetPath, url.origin);
  const upstream = await env.ASSETS.fetch(new Request(assetUrl.toString(), { method: "GET", headers: request.headers }));
  const headers = new Headers(upstream.headers);
  headers.set("referrer-policy", NO_REFERRER);
  if (isIndex && upstream.ok) {
    // Inject same-origin RemoteConfig into the bundle's fixed config tag. The
    // bundle ships unmodified; this is the only server-side rewrite.
    const html = injectRemoteConfig(await upstream.text(), env, url);
    return new Response(html, { status: upstream.status, headers });
  }
  return new Response(upstream.body, { status: upstream.status, headers });
}

function injectRemoteConfig(html: string, env: Env, url: URL): string {
  const config = {
    relayBaseUrl: String(env.RELAY_PUBLIC_ORIGIN || url.origin),
    daemonId: String(env.RELAY_DAEMON_ID || ""),
    authMode: "cookie",
  };
  const payload = JSON.stringify(config);
  if (!CONFIG_TAG_RE.test(html)) return html;
  return html.replace(CONFIG_TAG_RE, (_match, open: string, _body: string, close: string) => `${open}${payload}${close}`);
}

function randomToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

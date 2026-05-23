import { parseRelayEnvelope } from "../../core/protocol.js";
import { errorMessage } from "../../core/errors.js";
import {
  authErrorStatus,
  authenticateClientSession,
  hashSecret,
  isActiveSession,
  sanitizeClientEnvelopeForV1,
} from "../../core/auth.js";
import { D1RelayStore } from "./d1Store.js";
export { DaemonRendezvousDurableObject } from "./durableObjectCoordinator.js";

// Same-origin remote UI mount + login cookie (Channels go-live §2b, same-origin
// topology decision-f1f446b96707): the Worker static-serves the remote bundle
// under /app/ and sets the HttpOnly torque_session cookie from a valid Path-A
// client-session token. Same-origin means SameSite=Lax + no CORS, which sidesteps
// the third-party-cookie phaseout that would break a cross-site cookie WS upgrade.
const APP_PREFIX = "/app";
const SESSION_COOKIE = "torque_session";

export interface Env {
  RELAY_DB: D1Database;
  RENDEZVOUS: DurableObjectNamespace;
  // Static assets binding for the remote bundle (ee/frontend/remote). Optional so
  // non-asset deployments/tests that never hit /app still type-check.
  ASSETS?: Fetcher;
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

  // Same-origin session establish: trade a valid raw client-session token for the
  // HttpOnly login cookie. POST-only so the token rides the body, never the URL
  // (no browser-history / referrer / access-log leak of the secret).
  if (request.method === "POST" && url.pathname === "/session") {
    return handleSessionEstablish(request, store);
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

async function handleSessionEstablish(request: Request, store: D1RelayStore): Promise<Response> {
  const token = await readSessionToken(request);
  if (!token) {
    return json({ type: "error", message: "client session token is required", code: "missing_client_auth" }, 400);
  }
  // Reuse the exact validation the WS/HTTP auth path uses — no parallel check.
  const session = await store.getClientSessionByTokenHash(await hashSecret(token));
  if (!isActiveSession(session)) {
    return json({ type: "error", message: "client session is not active", code: "invalid_client_session" }, 401);
  }
  // HttpOnly: JS can never read the token post-establish. SameSite=Lax is
  // sufficient same-origin and avoids the third-party-cookie phaseout. Secure so
  // it only rides https/wss. Path=/ so it is sent on the /v1/client WS upgrade.
  const cookie = `${SESSION_COOKIE}=${encodeURIComponent(token)}; HttpOnly; Secure; SameSite=Lax; Path=/`;
  return new Response(null, {
    status: 303,
    headers: { "set-cookie": cookie, location: `${APP_PREFIX}/` },
  });
}

async function readSessionToken(request: Request): Promise<string> {
  const contentType = (request.headers.get("content-type") || "").toLowerCase();
  if (contentType.includes("application/json")) {
    const body = await request.json().catch(() => ({})) as { token?: unknown };
    return String(body?.token || "").trim();
  }
  // Default to form-encoded so a plain same-origin HTML <form> works.
  const params = new URLSearchParams(await request.text());
  return String(params.get("token") || "").trim();
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
    return new Response(null, { status: 308, headers: { location: `${APP_PREFIX}/` } });
  }
  let assetPath = url.pathname.slice(APP_PREFIX.length); // "/" or "/js/app.js"
  if (assetPath === "/") assetPath = "/index.html";
  const assetUrl = new URL(assetPath, url.origin);
  return env.ASSETS.fetch(new Request(assetUrl.toString(), { method: "GET", headers: request.headers }));
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

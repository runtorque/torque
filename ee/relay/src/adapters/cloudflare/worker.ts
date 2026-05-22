import { parseRelayEnvelope } from "../../core/protocol.js";
import { errorMessage } from "../../core/errors.js";
import { D1RelayStore } from "./d1Store.js";
export { DaemonRendezvousDurableObject } from "./durableObjectCoordinator.js";

export interface Env {
  RELAY_DB: D1Database;
  RENDEZVOUS: DurableObjectNamespace;
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
    if (url.searchParams.get("migrate") === "1") {
      await store.migrate();
    }
    return json({
      type: "ok",
      relay: "torque-ee-relay",
      storage: "d1",
      coordination: "durable-object",
      protocol_version: 1,
    });
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
    const envelope = parseRelayEnvelope(await request.json());
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

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

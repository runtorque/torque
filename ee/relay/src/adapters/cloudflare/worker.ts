import { D1RelayStore } from "./d1Store.js";
export { DaemonRendezvousDurableObject } from "./durableObjectCoordinator.js";

export interface Env {
  RELAY_DB: D1Database;
  RENDEZVOUS: DurableObjectNamespace;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      if (url.searchParams.get("migrate") === "1") {
        await new D1RelayStore(env.RELAY_DB).migrate();
      }
      return json({
        type: "ok",
        relay: "torque-ee-relay-spike",
        storage: "d1",
        coordination: "durable-object",
      });
    }

    const route = url.pathname.match(/^\/v1\/(?:daemon|client)\/([^/]+)\/ws$/);
    if (route && request.headers.get("upgrade")?.toLowerCase() === "websocket") {
      const daemonId = decodeURIComponent(route[1]);
      const id = env.RENDEZVOUS.idFromName(daemonId);
      return env.RENDEZVOUS.get(id).fetch(request);
    }

    return json({ type: "error", message: "not found" }, 404);
  },
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

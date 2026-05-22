import {
  makeErrorEnvelope,
  makeRelayEnvelope,
  parseRelayEnvelope,
  type RelayEnvelope,
} from "../../core/protocol.js";
import type { RelaySocket } from "../../core/ports.js";
import { toJsonObject } from "../../core/runtime.js";
import { errorMessage } from "../../core/errors.js";
import { StandaloneRegistryCoordinator } from "../standalone/registryCoordinator.js";

export type DurableObjectSessionAttachment = {
  role: "daemon" | "client";
  daemonId: string;
  clientId: string;
  connectionId: string;
  epoch: number;
};

export class DaemonRendezvousDurableObject {
  private readonly registry = new StandaloneRegistryCoordinator();
  private rehydrated = false;

  constructor(private readonly state: DurableObjectState, private readonly env: unknown) {
    void this.env;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname.match(/^\/internal\/route-to-daemon\/([^/]+)$/)) {
      await this.rehydrateFromHibernatedSockets();
      const match = url.pathname.match(/^\/internal\/route-to-daemon\/([^/]+)$/);
      const daemonId = decodeURIComponent(match?.[1] || "");
      const envelope = parseRelayEnvelope(await request.json());
      const delivery = await this.registry.sendToDaemon(daemonId, envelope);
      return json({ type: "ok", delivery });
    }

    if (request.headers.get("upgrade")?.toLowerCase() !== "websocket") {
      return new Response("Expected WebSocket", { status: 426 });
    }
    await this.rehydrateFromHibernatedSockets();
    const route = parseDurableObjectWsRoute(url);
    if (!route) return new Response("Not found", { status: 404 });

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair) as [WebSocket, WebSocket];
    const connectionId = `${route.role}:${route.daemonId}:${route.clientId || "daemon"}:${crypto.randomUUID()}`;
    const attachment: DurableObjectSessionAttachment = {
      role: route.role,
      daemonId: route.daemonId,
      clientId: route.clientId,
      connectionId,
      epoch: 0,
    };
    server.serializeAttachment?.(attachment);
    this.state.acceptWebSocket(server);
    await this.attachSocket(server, attachment);
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws: WebSocket, message: ArrayBuffer | string): Promise<void> {
    await this.rehydrateFromHibernatedSockets();
    const attachment = socketAttachment(ws);
    if (!attachment) {
      ws.close(1011, "missing_session_attachment");
      return;
    }
    let envelope: RelayEnvelope | null = null;
    try {
      const text = typeof message === "string" ? message : new TextDecoder().decode(message);
      envelope = parseRelayEnvelope(JSON.parse(text));
      if (attachment.role === "daemon") {
        if (!await this.registry.isCurrentDaemonConnection(attachment.daemonId, attachment.connectionId, attachment.epoch)) {
          ws.close(4001, "stale_daemon_connection");
          return;
        }
        await this.registry.broadcastToClients(attachment.daemonId, envelope);
      } else {
        await this.registry.sendToDaemon(attachment.daemonId, envelope);
      }
    } catch (error) {
      const err = makeErrorEnvelope({
        daemon_id: attachment.daemonId || "relay",
        source: { kind: "relay", id: "cloudflare-do" },
        target: { kind: attachment.role === "daemon" ? "daemon" : "remote-client", id: attachment.role === "daemon" ? attachment.daemonId : attachment.clientId || attachment.connectionId },
        code: "durable_object_message_error",
        message: errorMessage(error),
        ref_id: envelope?.id,
      });
      withSuppressedSend(ws, err);
    }
  }

  async webSocketClose(ws: WebSocket): Promise<void> {
    const attachment = socketAttachment(ws);
    if (attachment) this.registry.detach(attachment.connectionId);
  }

  async webSocketError(ws: WebSocket): Promise<void> {
    await this.webSocketClose(ws);
  }

  async rehydrateHibernatedSocketsForTest(): Promise<void> {
    await this.rehydrateFromHibernatedSockets();
  }

  snapshotForTest(daemonId: string) {
    return this.registry.snapshot(daemonId);
  }

  private async attachSocket(ws: WebSocket, attachment: DurableObjectSessionAttachment): Promise<void> {
    const socket = new CloudflareRelaySocket(attachment.connectionId, ws);
    if (attachment.role === "daemon") {
      const result = await this.registry.attachDaemon({ daemonId: attachment.daemonId, socket });
      attachment.epoch = result.epoch;
      ws.serializeAttachment?.(attachment);
      socket.sendControl("ready", result);
    } else {
      const result = await this.registry.attachClient({
        daemonId: attachment.daemonId,
        clientId: attachment.clientId,
        socket,
      });
      attachment.epoch = result.epoch;
      ws.serializeAttachment?.(attachment);
      socket.sendControl("ready", result);
    }
  }

  private async rehydrateFromHibernatedSockets(): Promise<void> {
    if (this.rehydrated) return;
    this.rehydrated = true;
    for (const ws of this.state.getWebSockets()) {
      const attachment = socketAttachment(ws);
      if (!attachment) continue;
      await this.attachSocket(ws, attachment);
    }
  }
}

class CloudflareRelaySocket implements RelaySocket {
  constructor(readonly id: string, private readonly ws: WebSocket) {}

  sendEnvelope(envelope: RelayEnvelope): void {
    this.ws.send(JSON.stringify(envelope));
  }

  sendControl(kind: "ready" | "error", payload: object): void {
    const payloadRecord = payload as Record<string, unknown>;
    this.sendEnvelope(makeRelayEnvelope({
      daemon_id: String(payloadRecord.daemonId || payloadRecord.daemon_id || "relay"),
      source: { kind: "relay", id: "cloudflare-do" },
      target: { kind: "remote-client", id: this.id },
      kind,
      payload: toJsonObject(payloadRecord),
    }));
  }

  close(code?: number, reason?: string): void {
    this.ws.close(code, reason);
  }
}

function parseDurableObjectWsRoute(url: URL): { role: "daemon" | "client"; daemonId: string; clientId: string } | null {
  const daemonMatch = url.pathname.match(/^\/v1\/daemon\/([^/]+)\/ws$/);
  if (daemonMatch) {
    return { role: "daemon", daemonId: decodeURIComponent(daemonMatch[1]), clientId: "" };
  }
  const clientMatch = url.pathname.match(/^\/v1\/client\/([^/]+)\/ws$/);
  if (clientMatch) {
    return {
      role: "client",
      daemonId: decodeURIComponent(clientMatch[1]),
      clientId: url.searchParams.get("client_id") || crypto.randomUUID(),
    };
  }
  return null;
}

function socketAttachment(ws: WebSocket): DurableObjectSessionAttachment | null {
  const value = ws.deserializeAttachment?.() as DurableObjectSessionAttachment | undefined;
  if (!value || !value.role || !value.daemonId || !value.connectionId) return null;
  return value;
}

function withSuppressedSend(ws: WebSocket, envelope: RelayEnvelope): void {
  try {
    ws.send(JSON.stringify(envelope));
  } catch {
    // Best-effort protocol errors must not crash the Durable Object.
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

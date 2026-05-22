import { makeRelayEnvelope, parseRelayEnvelope, type JsonObject, type JsonValue, type RelayEnvelope } from "../../core/protocol.js";
import type { RelaySocket } from "../../core/ports.js";
import { StandaloneRegistryCoordinator } from "../standalone/registryCoordinator.js";

export type DurableObjectSessionAttachment = {
  role: "daemon" | "client";
  daemonId: string;
  clientId: string;
  connectionId: string;
};

export class DaemonRendezvousDurableObject {
  private readonly registry = new StandaloneRegistryCoordinator();
  private rehydrated = false;

  constructor(private readonly state: DurableObjectState, private readonly env: unknown) {
    void this.env;
  }

  async fetch(request: Request): Promise<Response> {
    if (request.headers.get("upgrade")?.toLowerCase() !== "websocket") {
      return new Response("Expected WebSocket", { status: 426 });
    }
    this.rehydrateFromHibernatedSockets();
    const route = parseDurableObjectWsRoute(new URL(request.url));
    if (!route) return new Response("Not found", { status: 404 });

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair) as [WebSocket, WebSocket];
    const connectionId = `${route.role}:${route.daemonId}:${route.clientId || "daemon"}:${crypto.randomUUID()}`;
    const attachment: DurableObjectSessionAttachment = {
      role: route.role,
      daemonId: route.daemonId,
      clientId: route.clientId,
      connectionId,
    };
    server.serializeAttachment?.(attachment);
    this.state.acceptWebSocket(server);
    await this.attachSocket(server, attachment);
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws: WebSocket, message: ArrayBuffer | string): Promise<void> {
    this.rehydrateFromHibernatedSockets();
    const attachment = socketAttachment(ws);
    if (!attachment) {
      ws.close(1011, "missing_session_attachment");
      return;
    }
    const text = typeof message === "string" ? message : new TextDecoder().decode(message);
    const envelope = parseRelayEnvelope(JSON.parse(text));
    if (attachment.role === "daemon") {
      await this.registry.broadcastToClients(attachment.daemonId, envelope);
    } else {
      await this.registry.sendToDaemon(attachment.daemonId, envelope);
    }
  }

  async webSocketClose(ws: WebSocket): Promise<void> {
    const attachment = socketAttachment(ws);
    if (attachment) this.registry.detach(attachment.connectionId);
  }

  async webSocketError(ws: WebSocket): Promise<void> {
    await this.webSocketClose(ws);
  }

  private async attachSocket(ws: WebSocket, attachment: DurableObjectSessionAttachment): Promise<void> {
    const socket = new CloudflareRelaySocket(attachment.connectionId, ws);
    if (attachment.role === "daemon") {
      const result = await this.registry.attachDaemon({ daemonId: attachment.daemonId, socket });
      socket.sendControl("ready", result);
    } else {
      const result = await this.registry.attachClient({
        daemonId: attachment.daemonId,
        clientId: attachment.clientId,
        socket,
      });
      socket.sendControl("ready", result);
    }
  }

  private rehydrateFromHibernatedSockets(): void {
    if (this.rehydrated) return;
    this.rehydrated = true;
    for (const ws of this.state.getWebSockets()) {
      const attachment = socketAttachment(ws);
      if (!attachment) continue;
      void this.attachSocket(ws, attachment);
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

function toJsonObject(value: Record<string, unknown>): JsonObject {
  const output: JsonObject = {};
  for (const [key, item] of Object.entries(value)) {
    if (item === undefined) continue;
    output[key] = toJsonValue(item);
  }
  return output;
}

function toJsonValue(value: unknown): JsonValue {
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  if (Array.isArray(value)) return value.map(toJsonValue);
  if (value && typeof value === "object") return toJsonObject(value as Record<string, unknown>);
  return String(value);
}

import http from "node:http";
import { fileURLToPath } from "node:url";
import WebSocket, { WebSocketServer } from "ws";

import { makeRelayEnvelope, parseRelayEnvelope, type JsonObject, type JsonValue, type RelayEnvelope } from "../../core/protocol.js";
import type { RelayDirection, RelaySocket } from "../../core/ports.js";
import { SqliteRelayStore } from "./sqliteStore.js";
import { StandaloneRegistryCoordinator } from "./registryCoordinator.js";

export interface StandaloneRelayServerOptions {
  port?: number;
  host?: string;
  databasePath?: string;
}

export interface StandaloneRelayServerHandle {
  listen(): Promise<void>;
  close(): Promise<void>;
  server: http.Server;
  store: SqliteRelayStore;
  coordinator: StandaloneRegistryCoordinator;
}

export async function createStandaloneRelayServer(
  options: StandaloneRelayServerOptions = {},
): Promise<StandaloneRelayServerHandle> {
  const store = new SqliteRelayStore(options.databasePath || process.env.TORQUE_RELAY_DB || ":memory:");
  await store.migrate();
  const coordinator = new StandaloneRegistryCoordinator();
  const wss = new WebSocketServer({ noServer: true });

  const server = http.createServer(async (req, res) => {
    try {
      await handleHttpRequest(req, res, store, coordinator);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      writeJson(res, 500, { type: "error", message });
    }
  });

  server.on("upgrade", (req, socket, head) => {
    const route = parseWsRoute(req.url || "");
    if (!route) {
      socket.write("HTTP/1.1 404 Not Found\r\n\r\n");
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => {
      void attachRelayWebSocket(ws, route, store, coordinator);
    });
  });

  return {
    server,
    store,
    coordinator,
    listen: () => new Promise<void>((resolve) => {
      server.listen(options.port || Number(process.env.PORT || 8787), options.host || "127.0.0.1", resolve);
    }),
    close: () => new Promise<void>((resolve, reject) => {
      wss.close();
      server.close((error) => {
        void store.close();
        if (error) reject(error);
        else resolve();
      });
    }),
  };
}

async function handleHttpRequest(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  store: SqliteRelayStore,
  coordinator: StandaloneRegistryCoordinator,
): Promise<void> {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  if (req.method === "GET" && url.pathname === "/health") {
    writeJson(res, 200, {
      type: "ok",
      relay: "torque-ee-relay-spike",
      storage: store.kind,
      coordination: coordinator.kind,
    });
    return;
  }

  const postMessage = url.pathname.match(/^\/v1\/messages\/([^/]+)$/);
  if (req.method === "POST" && postMessage) {
    const daemonId = decodeURIComponent(postMessage[1]);
    const envelope = parseRelayEnvelope(await readJson(req));
    if (envelope.daemon_id !== daemonId) {
      writeJson(res, 400, { type: "error", message: "daemon_id path/envelope mismatch" });
      return;
    }
    const saved = await store.appendMessage(envelope, "to_daemon");
    const delivered = await coordinator.sendToDaemon(daemonId, envelope);
    if (delivered) await store.markDelivered(envelope.id);
    writeJson(res, 202, { type: "ok", delivered, message: saved });
    return;
  }

  writeJson(res, 404, { type: "error", message: "not found" });
}

type WsRoute = {
  role: "daemon" | "client";
  daemonId: string;
  clientId: string;
};

function parseWsRoute(rawUrl: string): WsRoute | null {
  const url = new URL(rawUrl || "/", "http://127.0.0.1");
  const daemonMatch = url.pathname.match(/^\/v1\/daemon\/([^/]+)\/ws$/);
  if (daemonMatch) {
    return {
      role: "daemon",
      daemonId: decodeURIComponent(daemonMatch[1]),
      clientId: "",
    };
  }
  const clientMatch = url.pathname.match(/^\/v1\/client\/([^/]+)\/ws$/);
  if (clientMatch) {
    return {
      role: "client",
      daemonId: decodeURIComponent(clientMatch[1]),
      clientId: url.searchParams.get("client_id") || `client-${Math.random().toString(36).slice(2, 10)}`,
    };
  }
  return null;
}

async function attachRelayWebSocket(
  ws: WebSocket,
  route: WsRoute,
  store: SqliteRelayStore,
  coordinator: StandaloneRegistryCoordinator,
): Promise<void> {
  const connectionId = `${route.role}:${route.daemonId}:${route.clientId || "daemon"}:${Math.random().toString(36).slice(2, 10)}`;
  const socket = new WsRelaySocket(connectionId, ws);
  if (route.role === "daemon") {
    const result = await coordinator.attachDaemon({ daemonId: route.daemonId, socket });
    socket.sendControl("ready", result);
  } else {
    const result = await coordinator.attachClient({ daemonId: route.daemonId, clientId: route.clientId, socket });
    socket.sendControl("ready", result);
  }

  ws.on("message", (data) => {
    void handleRelayWsMessage(data, route, store, coordinator);
  });
  ws.on("close", () => {
    coordinator.detach(connectionId);
  });
}

async function handleRelayWsMessage(
  data: WebSocket.RawData,
  route: WsRoute,
  store: SqliteRelayStore,
  coordinator: StandaloneRegistryCoordinator,
): Promise<void> {
  const text = data.toString("utf8");
  const envelope = parseRelayEnvelope(JSON.parse(text));
  const direction: RelayDirection = route.role === "daemon" ? "from_daemon" : "to_daemon";
  await store.appendMessage(envelope, direction);
  if (route.role === "daemon") {
    const count = await coordinator.broadcastToClients(route.daemonId, envelope);
    if (count > 0) await store.markDelivered(envelope.id);
  } else {
    const delivered = await coordinator.sendToDaemon(route.daemonId, envelope);
    if (delivered) await store.markDelivered(envelope.id);
  }
}

class WsRelaySocket implements RelaySocket {
  constructor(readonly id: string, private readonly ws: WebSocket) {}

  sendEnvelope(envelope: RelayEnvelope): void {
    if (this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(envelope));
    }
  }

  sendControl(kind: "ready" | "error", payload: object): void {
    const payloadRecord = payload as Record<string, unknown>;
    this.sendEnvelope(makeRelayEnvelope({
      daemon_id: String(payloadRecord.daemonId || payloadRecord.daemon_id || "relay"),
      source: { kind: "relay", id: "standalone" },
      target: { kind: "remote-client", id: this.id },
      kind,
      payload: toJsonObject(payloadRecord),
    }));
  }

  close(code?: number, reason?: string): void {
    if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
      this.ws.close(code, reason);
    }
  }
}

async function readJson(req: http.IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  const text = Buffer.concat(chunks).toString("utf8");
  return text ? JSON.parse(text) : {};
}

function writeJson(res: http.ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.statusCode = status;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.setHeader("content-length", Buffer.byteLength(payload));
  res.end(payload);
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

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) {
  const handle = await createStandaloneRelayServer();
  await handle.listen();
  const address = handle.server.address();
  const label = typeof address === "object" && address
    ? `${address.address}:${address.port}`
    : String(address || "listening");
  console.log(`Torque EE relay spike listening on ${label}`);
}

import http from "node:http";
import { fileURLToPath } from "node:url";
import WebSocket, { WebSocketServer } from "ws";

import { RelayRuntime, toJsonObject } from "../../core/runtime.js";
import { parseRelayEnvelope, parseRelayEnvelopeJson, type JsonObject, type RelayEnvelope } from "../../core/protocol.js";
import type { RelaySocket } from "../../core/ports.js";
import { errorMessage } from "../../core/errors.js";
import { SqliteRelayStore } from "./sqliteStore.js";
import { StandaloneRegistryCoordinator } from "./registryCoordinator.js";

export interface StandaloneRelayServerOptions {
  port?: number;
  host?: string;
  databasePath?: string;
  replayLimit?: number;
}

export interface StandaloneRelayServerHandle {
  listen(): Promise<void>;
  close(): Promise<void>;
  server: http.Server;
  store: SqliteRelayStore;
  coordinator: StandaloneRegistryCoordinator;
  runtime: RelayRuntime;
}

export async function createStandaloneRelayServer(
  options: StandaloneRelayServerOptions = {},
): Promise<StandaloneRelayServerHandle> {
  const store = new SqliteRelayStore(options.databasePath || process.env.TORQUE_RELAY_DB || ":memory:");
  await store.migrate();
  const coordinator = new StandaloneRegistryCoordinator();
  const runtime = new RelayRuntime(store, coordinator, {
    replayLimit: options.replayLimit,
    relayId: "standalone",
  });
  const wss = new WebSocketServer({ noServer: true });

  const server = http.createServer(async (req, res) => {
    try {
      await handleHttpRequest(req, res, runtime, store, coordinator);
    } catch (error) {
      writeJson(res, 500, { type: "error", message: errorMessage(error) });
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
      void attachRelayWebSocket(ws, route, runtime);
    });
  });

  return {
    server,
    store,
    coordinator,
    runtime,
    listen: () => new Promise<void>((resolve) => {
      server.listen(options.port ?? Number(process.env.PORT || 8787), options.host || "127.0.0.1", resolve);
    }),
    close: () => new Promise<void>((resolve, reject) => {
      for (const client of wss.clients) {
        client.terminate();
      }
      wss.close((wssError) => {
        server.close((serverError) => {
          void store.close();
          const error = wssError || serverError;
          if (error) reject(error);
          else resolve();
        });
      });
    }),
  };
}

async function handleHttpRequest(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  runtime: RelayRuntime,
  store: SqliteRelayStore,
  coordinator: StandaloneRegistryCoordinator,
): Promise<void> {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  if (req.method === "GET" && url.pathname === "/health") {
    writeJson(res, 200, {
      type: "ok",
      relay: "torque-ee-relay",
      storage: store.kind,
      coordination: coordinator.kind,
      protocol_version: 1,
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
    const result = await runtime.handleFromClient(envelope);
    writeJson(res, 202, {
      type: "ok",
      delivered: Boolean(result.delivery && "delivered" in result.delivery
        ? result.delivery.delivered
        : false),
      idempotent: result.idempotent,
      message: result.stored,
      delivery: result.delivery || null,
    });
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
  runtime: RelayRuntime,
): Promise<void> {
  const connectionId = `${route.role}:${route.daemonId}:${route.clientId || "daemon"}:${Math.random().toString(36).slice(2, 10)}`;
  const socket = new WsRelaySocket(connectionId, ws);
  let epoch = 0;
  try {
    if (route.role === "daemon") {
      const result = await runtime.attachDaemon(route.daemonId, socket);
      epoch = result.epoch;
      socket.sendEnvelope(runtime.makeReadyEnvelope(route.daemonId, connectionId, resultToPayload(result)));
    } else {
      const result = await runtime.attachClient(route.daemonId, route.clientId, socket);
      epoch = result.epoch;
      socket.sendEnvelope(runtime.makeReadyEnvelope(route.daemonId, connectionId, resultToPayload(result)));
    }
  } catch (error) {
    socket.sendEnvelope(runtime.makeErrorEnvelope(route.daemonId, connectionId, error));
    socket.close(1011, "attach_failed");
    return;
  }

  ws.on("message", (data) => {
    void handleRelayWsMessage(data, route, connectionId, epoch, runtime, socket);
  });
  ws.on("close", () => {
    void runtime.coordinator.detach(connectionId);
  });
}

async function handleRelayWsMessage(
  data: WebSocket.RawData,
  route: WsRoute,
  connectionId: string,
  epoch: number,
  runtime: RelayRuntime,
  socket: WsRelaySocket,
): Promise<void> {
  let envelope: RelayEnvelope | null = null;
  try {
    envelope = parseRelayEnvelopeJson(data.toString("utf8"));
    if (route.role === "daemon") {
      await runtime.handleFromDaemon(connectionId, epoch, envelope);
    } else {
      await runtime.handleFromClient(envelope);
    }
  } catch (error) {
    socket.sendEnvelope(runtime.makeErrorEnvelope(route.daemonId, connectionId, error, envelope?.id || ""));
  }
}

class WsRelaySocket implements RelaySocket {
  constructor(readonly id: string, private readonly ws: WebSocket) {}

  sendEnvelope(envelope: RelayEnvelope): void {
    if (this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(envelope));
    }
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

function resultToPayload(result: object): JsonObject {
  return toJsonObject({ ...(result as Record<string, unknown>) });
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) {
  const handle = await createStandaloneRelayServer();
  await handle.listen();
  const address = handle.server.address();
  const label = typeof address === "object" && address
    ? `${address.address}:${address.port}`
    : String(address || "listening");
  console.log(`Torque EE relay listening on ${label}`);
}

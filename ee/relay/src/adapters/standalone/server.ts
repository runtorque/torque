import http from "node:http";
import { fileURLToPath } from "node:url";
import WebSocket, { WebSocketServer } from "ws";

import { RelayRuntime, toJsonObject } from "../../core/runtime.js";
import { parseRelayEnvelope, parseRelayEnvelopeJson, type JsonObject, type RelayEnvelope } from "../../core/protocol.js";
import type { ClientAuthPrincipal, DaemonAuthPrincipal, RelaySocket } from "../../core/ports.js";
import { errorMessage } from "../../core/errors.js";
import {
  LOCAL_DEV_AUTH_MODE,
  authErrorStatus,
  authenticateClientSession,
  authenticateDaemonAttach,
  hashSecret,
  localDevClientPrincipal,
  localDevDaemonPrincipal,
  sanitizeClientEnvelopeForV1,
  selectStandaloneAuthMode,
  type StandaloneAuthMode,
} from "../../core/auth.js";
import { nowIso } from "../../core/sql.js";
import { SqliteRelayStore } from "./sqliteStore.js";
import { StandaloneRegistryCoordinator } from "./registryCoordinator.js";

export interface StandaloneRelayServerOptions {
  port?: number;
  host?: string;
  databasePath?: string;
  replayLimit?: number;
  authMode?: StandaloneAuthMode;
  bootstrapToken?: string;
}

export interface StandaloneRelayServerHandle {
  listen(): Promise<void>;
  close(): Promise<void>;
  server: http.Server;
  store: SqliteRelayStore;
  coordinator: StandaloneRegistryCoordinator;
  runtime: RelayRuntime;
  authMode: StandaloneAuthMode;
}

export async function createStandaloneRelayServer(
  options: StandaloneRelayServerOptions = {},
): Promise<StandaloneRelayServerHandle> {
  const host = options.host || "127.0.0.1";
  const requestedAuthMode = options.authMode || process.env.TORQUE_RELAY_AUTH_MODE || "";
  const authMode = selectStandaloneAuthMode({ host, requestedMode: requestedAuthMode });
  const store = new SqliteRelayStore(options.databasePath || process.env.TORQUE_RELAY_DB || ":memory:");
  await store.migrate();
  const coordinator = new StandaloneRegistryCoordinator();
  const runtime = new RelayRuntime(store, coordinator, {
    replayLimit: options.replayLimit,
    relayId: "standalone",
  });
  const bootstrapToken = String(options.bootstrapToken || process.env.TORQUE_RELAY_BOOTSTRAP_TOKEN || "").trim();
  const wss = new WebSocketServer({ noServer: true });

  const server = http.createServer(async (req, res) => {
    try {
      await handleHttpRequest(req, res, runtime, store, coordinator, authMode, bootstrapToken);
    } catch (error) {
      writeJson(res, 500, { type: "error", message: errorMessage(error) });
    }
  });

  server.on("upgrade", (req, socket, head) => {
    void (async () => {
      const route = parseWsRoute(req.url || "");
      if (!route) {
        socket.write("HTTP/1.1 404 Not Found\r\n\r\n");
        socket.destroy();
        return;
      }
      let auth: DaemonAuthPrincipal | ClientAuthPrincipal;
      try {
        auth = await authenticateWsRoute(req, route, store, authMode);
      } catch (error) {
        const status = authErrorStatus(error);
        socket.write(`HTTP/1.1 ${status} ${status === 403 ? "Forbidden" : "Unauthorized"}\r\nConnection: close\r\n\r\n`);
        socket.destroy();
        return;
      }
      wss.handleUpgrade(req, socket, head, (ws) => {
        void attachRelayWebSocket(ws, route, runtime, auth);
      });
    })();
  });

  return {
    server,
    store,
    coordinator,
    runtime,
    authMode,
    listen: () => new Promise<void>((resolve) => {
      server.listen(options.port ?? Number(process.env.PORT || 8787), host, resolve);
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
  authMode: StandaloneAuthMode,
  bootstrapToken: string,
): Promise<void> {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  if (req.method === "GET" && url.pathname === "/health") {
    writeJson(res, 200, {
      type: "ok",
      relay: "torque-ee-relay",
      storage: store.kind,
      coordination: coordinator.kind,
      protocol_version: 1,
      auth_mode: authMode,
    });
    return;
  }

  if (req.method === "POST" && url.pathname === "/v1/admin/pairing-tokens") {
    if (!await verifyBootstrap(req, bootstrapToken, authMode)) {
      writeJson(res, 401, { type: "error", message: "bootstrap authorization required" });
      return;
    }
    const body = await readJson(req) as Record<string, unknown>;
    const token = randomToken("pair");
    const now = nowIso();
    const expiresAt = String(body.expires_at || "") || new Date(Date.now() + 15 * 60 * 1000).toISOString();
    const record = await store.createPairingToken({
      id: String(body.id || `pairing-${crypto.randomUUID()}`),
      token_hash: await hashSecret(token),
      owner_user_id: cleanRequired(String(body.owner_user_id || ""), "owner_user_id"),
      daemon_id: String(body.daemon_id || ""),
      label: String(body.label || ""),
      created_at: now,
      expires_at: expiresAt,
      consumed_at: "",
      revoked_at: "",
      metadata: {},
    });
    writeJson(res, 201, { type: "ok", pairing_token: token, record });
    return;
  }

  if (req.method === "POST" && url.pathname === "/v1/pair") {
    const body = await readJson(req) as Record<string, unknown>;
    const pairingToken = String(body.pairing_token || "").trim();
    if (!pairingToken) {
      writeJson(res, 401, { type: "error", message: "pairing token is invalid" });
      return;
    }
    const tokenHash = await hashSecret(pairingToken);
    const pairing = await store.consumePairingToken(tokenHash);
    if (!pairing) {
      writeJson(res, 401, { type: "error", message: "pairing token is invalid" });
      return;
    }
    const daemonId = cleanRequired(String(body.daemon_id || pairing.daemon_id || ""), "daemon_id");
    if (pairing.daemon_id && pairing.daemon_id !== daemonId) {
      writeJson(res, 403, { type: "error", message: "pairing token daemon mismatch" });
      return;
    }
    const now = nowIso();
    const credential = await store.createDaemonCredential({
      credential_id: String(body.credential_id || `cred-${crypto.randomUUID()}`),
      daemon_id: daemonId,
      owner_user_id: pairing.owner_user_id,
      public_key_jwk: toJsonObject(body.public_key_jwk as Record<string, unknown> || {}),
      alg: "ES256",
      created_at: now,
      last_used_at: "",
      revoked_at: "",
      metadata: {},
    });
    await store.upsertInstance({
      id: daemonId,
      owner_user_id: pairing.owner_user_id,
      label: pairing.label || daemonId,
      created_at: now,
      last_seen_at: now,
      metadata: { paired_by: "standalone" },
    });
    writeJson(res, 201, { type: "ok", credential_id: credential.credential_id, daemon_id: daemonId, owner_user_id: pairing.owner_user_id });
    return;
  }

  if (req.method === "POST" && url.pathname === "/v1/admin/client-sessions") {
    if (!await verifyBootstrap(req, bootstrapToken, authMode)) {
      writeJson(res, 401, { type: "error", message: "bootstrap authorization required" });
      return;
    }
    const body = await readJson(req) as Record<string, unknown>;
    const token = randomToken("sess");
    const now = nowIso();
    const expiresAt = String(body.expires_at || "") || new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
    const session = await store.createClientSession({
      session_id: String(body.session_id || `session-${crypto.randomUUID()}`),
      token_hash: await hashSecret(token),
      owner_user_id: cleanRequired(String(body.owner_user_id || ""), "owner_user_id"),
      created_at: now,
      expires_at: expiresAt,
      revoked_at: "",
      metadata: {},
    });
    writeJson(res, 201, { type: "ok", client_session_token: token, session });
    return;
  }

  const postMessage = url.pathname.match(/^\/v1\/messages\/([^/]+)$/);
  if (req.method === "POST" && postMessage) {
    const daemonId = decodeURIComponent(postMessage[1]);
    let principal: ClientAuthPrincipal;
    try {
      principal = await authenticateHttpClient(req, daemonId, store, authMode);
    } catch (error) {
      writeJson(res, authErrorStatus(error), { type: "error", message: errorMessage(error), code: error instanceof Error && "code" in error ? (error as { code?: string }).code : "relay_auth_error" });
      return;
    }
    const envelope = sanitizeClientEnvelopeForV1(parseRelayEnvelope(await readJson(req)), principal);
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

async function authenticateWsRoute(
  req: http.IncomingMessage,
  route: WsRoute,
  store: SqliteRelayStore,
  authMode: StandaloneAuthMode,
): Promise<DaemonAuthPrincipal | ClientAuthPrincipal> {
  if (authMode === LOCAL_DEV_AUTH_MODE) {
    return route.role === "daemon" ? localDevDaemonPrincipal(route.daemonId) : localDevClientPrincipal();
  }
  const url = new URL(req.url || "/", "http://127.0.0.1");
  if (route.role === "daemon") {
    return authenticateDaemonAttach(store, {
      method: req.method || "GET",
      url,
      daemonId: route.daemonId,
      headers: incomingHeaders(req),
    });
  }
  return authenticateClientSession(store, {
    daemonId: route.daemonId,
    headers: incomingHeaders(req),
  });
}

async function authenticateHttpClient(
  req: http.IncomingMessage,
  daemonId: string,
  store: SqliteRelayStore,
  authMode: StandaloneAuthMode,
): Promise<ClientAuthPrincipal> {
  if (authMode === LOCAL_DEV_AUTH_MODE) return localDevClientPrincipal();
  return authenticateClientSession(store, { daemonId, headers: incomingHeaders(req) });
}

async function attachRelayWebSocket(
  ws: WebSocket,
  route: WsRoute,
  runtime: RelayRuntime,
  auth: DaemonAuthPrincipal | ClientAuthPrincipal,
): Promise<void> {
  const connectionId = `${route.role}:${route.daemonId}:${route.clientId || "daemon"}:${Math.random().toString(36).slice(2, 10)}`;
  const socket = new WsRelaySocket(connectionId, ws);
  let epoch = 0;
  try {
    if (route.role === "daemon") {
      const result = await runtime.attachDaemon(route.daemonId, socket, auth as DaemonAuthPrincipal);
      if (!result.accepted) throw new Error(result.reason || "daemon attach rejected");
      epoch = result.epoch;
      socket.sendEnvelope(runtime.makeReadyEnvelope(route.daemonId, connectionId, resultToPayload(result), "daemon"));
    } else {
      const result = await runtime.attachClient(route.daemonId, route.clientId, socket, auth as ClientAuthPrincipal);
      if (!result.accepted) throw new Error(result.reason || "client attach rejected");
      epoch = result.epoch;
      socket.sendEnvelope(runtime.makeReadyEnvelope(route.daemonId, connectionId, resultToPayload(result)));
    }
  } catch (error) {
    socket.sendEnvelope(runtime.makeErrorEnvelope(
      route.daemonId,
      connectionId,
      error,
      "",
      route.role === "daemon" ? "daemon" : "remote-client",
    ));
    socket.close(1011, "attach_failed");
    return;
  }

  ws.on("message", (data) => {
    void handleRelayWsMessage(data, route, connectionId, epoch, runtime, socket, auth);
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
  auth: DaemonAuthPrincipal | ClientAuthPrincipal,
): Promise<void> {
  let envelope: RelayEnvelope | null = null;
  try {
    envelope = parseRelayEnvelopeJson(data.toString("utf8"));
    if (route.role === "daemon") {
      await runtime.handleFromDaemon(connectionId, epoch, envelope);
    } else {
      await runtime.handleFromClient(sanitizeClientEnvelopeForV1(envelope, auth as ClientAuthPrincipal));
    }
  } catch (error) {
    socket.sendEnvelope(runtime.makeErrorEnvelope(
      route.daemonId,
      connectionId,
      error,
      envelope?.id || "",
      route.role === "daemon" ? "daemon" : "remote-client",
    ));
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

async function verifyBootstrap(req: http.IncomingMessage, bootstrapToken: string, authMode: StandaloneAuthMode): Promise<boolean> {
  if (authMode === LOCAL_DEV_AUTH_MODE && !bootstrapToken) return true;
  if (!bootstrapToken) return false;
  const supplied = String(incomingHeaders(req).get("authorization") || "").replace(/^Bearer\s+/i, "").trim();
  if (!supplied) return false;
  return await hashSecret(supplied) === await hashSecret(bootstrapToken);
}

function incomingHeaders(req: http.IncomingMessage): HeadersLikeImpl {
  return {
    get(name: string): string | null {
      const value = req.headers[name.toLowerCase()];
      if (Array.isArray(value)) return value.join(", ");
      return typeof value === "string" ? value : null;
    },
  };
}

type HeadersLikeImpl = { get(name: string): string | null };

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

function randomToken(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}-${Math.random().toString(36).slice(2)}`;
}

function cleanRequired(value: string, name: string): string {
  const text = String(value || "").trim();
  if (!text) throw new Error(`${name} is required`);
  return text;
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) {
  const handle = await createStandaloneRelayServer();
  await handle.listen();
  const address = handle.server.address();
  const label = typeof address === "object" && address
    ? `${address.address}:${address.port}`
    : String(address || "listening");
  console.log(`Torque EE relay listening on ${label} (auth_mode=${handle.authMode})`);
}

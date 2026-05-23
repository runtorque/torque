import {
  type RelayEndpoint,
  makeErrorEnvelope,
  makeRelayEnvelope,
  parseRelayEnvelope,
  type RelayEnvelope,
} from "../../core/protocol.js";
import type { ClientAuthPrincipal, DaemonAuthPrincipal, RelaySocket, RelayStore } from "../../core/ports.js";
import { toJsonObject } from "../../core/runtime.js";
import { errorMessage } from "../../core/errors.js";
import {
  authErrorStatus,
  authenticateClientSession,
  authenticateDaemonAttach,
  isActiveSession,
  makeAuthErrorEnvelope,
  sanitizeClientEnvelopeForV1,
} from "../../core/auth.js";
import { StandaloneRegistryCoordinator } from "../standalone/registryCoordinator.js";
import {
  MINT_CLIENT_ESTABLISH_CODE_KIND,
  makeMintResultEnvelope,
  mintClientEstablishCode,
} from "../../core/establishMint.js";
import { RelayError } from "../../core/errors.js";
import { D1RelayStore } from "./d1Store.js";

const RELAY_ID = "cloudflare-do";

export type DurableObjectSessionAttachment = {
  role: "daemon" | "client";
  daemonId: string;
  clientId: string;
  connectionId: string;
  epoch: number;
  ownerUserId?: string;
  credentialId?: string;
  sessionId?: string;
  userId?: string;
};

type DurableObjectEnv = {
  RELAY_DB?: D1Database;
  authStore?: RelayStore;
};

export class DaemonRendezvousDurableObject {
  private readonly registry = new StandaloneRegistryCoordinator();
  private rehydrated = false;

  constructor(private readonly state: DurableObjectState, private readonly env: DurableObjectEnv) {}

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

    let principal: DaemonAuthPrincipal | ClientAuthPrincipal;
    try {
      principal = await this.authenticateRoute(route, request);
    } catch (error) {
      return json({ type: "error", message: errorMessage(error), code: (error as { code?: string }).code || "relay_auth_error" }, authErrorStatus(error));
    }

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair) as [WebSocket, WebSocket];
    const connectionId = `${route.role}:${route.daemonId}:${route.clientId || "daemon"}:${crypto.randomUUID()}`;
    const attachment = attachmentForPrincipal(route, connectionId, principal);
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
        // Daemon-mediated mint of a client establish code (b2). Handled inline on
        // the authed daemon WS only — never stored or broadcast to clients; the
        // mint enforces owner-from-attach + replay/fencing/rate-limit server-side.
        if (envelope.kind === MINT_CLIENT_ESTABLISH_CODE_KIND) {
          await this.handleMintRequest(ws, attachment, envelope);
          return;
        }
        if (!await this.registry.isCurrentDaemonConnection(attachment.daemonId, attachment.connectionId, attachment.epoch)) {
          ws.close(4001, "stale_daemon_connection");
          return;
        }
        await this.registry.broadcastToClients(attachment.daemonId, envelope);
      } else {
        await this.registry.sendToDaemon(
          attachment.daemonId,
          sanitizeClientEnvelopeForV1(envelope, clientPrincipalFromAttachment(attachment)),
        );
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

  private async handleMintRequest(
    ws: WebSocket,
    attachment: DurableObjectSessionAttachment,
    envelope: RelayEnvelope,
  ): Promise<void> {
    try {
      const result = await mintClientEstablishCode({
        store: this.authStore(),
        coordinator: this.registry,
        daemonId: attachment.daemonId,
        principal: daemonPrincipalFromAttachment(attachment),
        connectionId: attachment.connectionId,
        epoch: attachment.epoch,
        payload: envelope.payload,
      });
      // The raw code rides the response envelope ONCE and is never stored/broadcast.
      withSuppressedSend(ws, makeMintResultEnvelope({
        daemonId: attachment.daemonId,
        relayId: RELAY_ID,
        result,
        refId: envelope.id,
        traceId: envelope.trace_id,
      }));
    } catch (error) {
      withSuppressedSend(ws, makeErrorEnvelope({
        daemon_id: attachment.daemonId || "relay",
        source: { kind: "relay", id: RELAY_ID },
        target: { kind: "daemon", id: attachment.daemonId },
        code: error instanceof RelayError ? error.code : "mint_establish_code_error",
        message: errorMessage(error),
        retryable: error instanceof RelayError ? error.retryable : false,
        ref_id: envelope.id,
      }));
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

  async attachSocketForTest(
    ws: WebSocket,
    route: { role: "daemon" | "client"; daemonId: string; clientId: string },
    request: Request,
  ): Promise<DurableObjectSessionAttachment> {
    const principal = await this.authenticateRoute(route, request);
    const attachment = attachmentForPrincipal(route, `${route.role}:${route.daemonId}:${route.clientId || "daemon"}:test`, principal);
    ws.serializeAttachment?.(attachment);
    await this.attachSocket(ws, attachment);
    return attachment;
  }

  snapshotForTest(daemonId: string) {
    return this.registry.snapshot(daemonId);
  }

  private async authenticateRoute(
    route: { role: "daemon" | "client"; daemonId: string; clientId: string },
    request: Request,
  ): Promise<DaemonAuthPrincipal | ClientAuthPrincipal> {
    const store = this.authStore();
    if (route.role === "daemon") {
      return authenticateDaemonAttach(store, {
        method: request.method,
        url: new URL(request.url),
        daemonId: route.daemonId,
        headers: request.headers,
      });
    }
    return authenticateClientSession(store, {
      daemonId: route.daemonId,
      headers: request.headers,
    });
  }

  private authStore(): RelayStore {
    if (this.env.authStore) return this.env.authStore;
    if (!this.env.RELAY_DB) throw new Error("RELAY_DB binding is required for Durable Object auth");
    return new D1RelayStore(this.env.RELAY_DB);
  }


  private async attachmentStillAuthorized(attachment: DurableObjectSessionAttachment): Promise<boolean> {
    const store = this.authStore();
    const instance = await store.getInstance(attachment.daemonId);
    if (!instance?.owner_user_id || instance.owner_user_id !== attachment.ownerUserId) return false;
    if (attachment.role === "daemon") {
      if (!attachment.credentialId) return false;
      const credential = await store.getDaemonCredential(attachment.daemonId, attachment.credentialId);
      return Boolean(credential && !credential.revoked_at && credential.owner_user_id === instance.owner_user_id);
    }
    if (!attachment.sessionId) return false;
    const session = await store.getClientSession(attachment.sessionId);
    return Boolean(isActiveSession(session) && session.owner_user_id === instance.owner_user_id);
  }

  private async attachSocket(ws: WebSocket, attachment: DurableObjectSessionAttachment): Promise<void> {
    const socket = new CloudflareRelaySocket(
      attachment.connectionId,
      ws,
      attachment.daemonId,
      controlTargetForAttachment(attachment),
    );
    if (attachment.role === "daemon") {
      const result = await this.registry.attachDaemon({
        daemonId: attachment.daemonId,
        socket,
        auth: daemonPrincipalFromAttachment(attachment),
      });
      if (!result.accepted) {
        socket.sendEnvelope(makeAuthErrorEnvelope(attachment.daemonId, attachment.daemonId, new Error(result.reason || "daemon attach rejected"), "daemon"));
        socket.close(4003, result.reason || "daemon_attach_rejected");
        return;
      }
      attachment.epoch = result.epoch;
      ws.serializeAttachment?.(attachment);
      socket.sendControl("ready", result);
    } else {
      const result = await this.registry.attachClient({
        daemonId: attachment.daemonId,
        clientId: attachment.clientId,
        socket,
        auth: clientPrincipalFromAttachment(attachment),
      });
      if (!result.accepted) {
        socket.sendEnvelope(makeAuthErrorEnvelope(attachment.daemonId, attachment.clientId, new Error(result.reason || "client attach rejected"), "remote-client"));
        socket.close(4003, result.reason || "client_attach_rejected");
        return;
      }
      attachment.epoch = result.epoch;
      ws.serializeAttachment?.(attachment);
      socket.sendControl("ready", result);
    }
  }

  private async rehydrateFromHibernatedSockets(): Promise<void> {
    if (this.rehydrated) return;
    this.rehydrated = true;
    for (const { ws, attachment } of hibernatedSocketEntries(this.state.getWebSockets())) {
      if (!attachment.ownerUserId || !await this.attachmentStillAuthorized(attachment)) {
        ws.close(4003, "authenticated_session_revoked");
        continue;
      }
      await this.attachSocket(ws, attachment);
    }
  }
}

class CloudflareRelaySocket implements RelaySocket {
  constructor(
    readonly id: string,
    private readonly ws: WebSocket,
    private readonly daemonId: string,
    private readonly controlTarget: RelayEndpoint,
  ) {}

  sendEnvelope(envelope: RelayEnvelope): void {
    this.ws.send(JSON.stringify(envelope));
  }

  sendControl(kind: "ready" | "error", payload: object): void {
    this.sendEnvelope(makeRelayEnvelope({
      daemon_id: this.daemonId || "relay",
      source: { kind: "relay", id: "cloudflare-do" },
      target: this.controlTarget,
      kind,
      payload: toJsonObject(payload as Record<string, unknown>),
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

function hibernatedSocketEntries(
  sockets: WebSocket[],
): Array<{ ws: WebSocket; attachment: DurableObjectSessionAttachment }> {
  return sockets
    .map((ws) => ({ ws, attachment: socketAttachment(ws) }))
    .filter((entry): entry is { ws: WebSocket; attachment: DurableObjectSessionAttachment } => Boolean(entry.attachment))
    .sort((left, right) => {
      // Rehydrate daemon owners in serialized epoch order so a stale hibernated
      // socket cannot replace the newer owner just because Cloudflare returned
      // getWebSockets() in reverse order. Clients attach after daemon election
      // so their ready payload observes the restored owner epoch.
      const roleOrder = rolePriority(left.attachment.role) - rolePriority(right.attachment.role);
      if (roleOrder) return roleOrder;
      const daemonOrder = left.attachment.daemonId.localeCompare(right.attachment.daemonId);
      if (daemonOrder) return daemonOrder;
      if (left.attachment.role === "daemon" && right.attachment.role === "daemon") {
        const epochOrder = Number(left.attachment.epoch || 0) - Number(right.attachment.epoch || 0);
        if (epochOrder) return epochOrder;
      }
      const clientOrder = left.attachment.clientId.localeCompare(right.attachment.clientId);
      if (clientOrder) return clientOrder;
      return left.attachment.connectionId.localeCompare(right.attachment.connectionId);
    });
}

function rolePriority(role: DurableObjectSessionAttachment["role"]): number {
  return role === "daemon" ? 0 : 1;
}

function controlTargetForAttachment(attachment: DurableObjectSessionAttachment): RelayEndpoint {
  return attachment.role === "daemon"
    ? { kind: "daemon", id: attachment.daemonId }
    : { kind: "remote-client", id: attachment.clientId || attachment.connectionId, user_id: attachment.userId || "user" };
}

function attachmentForPrincipal(
  route: { role: "daemon" | "client"; daemonId: string; clientId: string },
  connectionId: string,
  principal: DaemonAuthPrincipal | ClientAuthPrincipal,
): DurableObjectSessionAttachment {
  return {
    role: route.role,
    daemonId: route.daemonId,
    clientId: route.clientId,
    connectionId,
    epoch: 0,
    ownerUserId: principal.ownerUserId,
    credentialId: principal.kind === "daemon" ? principal.credentialId : undefined,
    sessionId: principal.kind === "client" ? principal.sessionId : undefined,
    userId: principal.kind === "client" ? principal.userId : undefined,
  };
}

function daemonPrincipalFromAttachment(attachment: DurableObjectSessionAttachment): DaemonAuthPrincipal {
  return {
    kind: "daemon",
    daemonId: attachment.daemonId,
    ownerUserId: attachment.ownerUserId || "",
    credentialId: attachment.credentialId || "",
    authMode: "signed-attach-v1",
  };
}

function clientPrincipalFromAttachment(attachment: DurableObjectSessionAttachment): ClientAuthPrincipal {
  return {
    kind: "client",
    ownerUserId: attachment.ownerUserId || "",
    sessionId: attachment.sessionId || "",
    userId: attachment.userId || "user",
    authMode: "session-v1",
  };
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

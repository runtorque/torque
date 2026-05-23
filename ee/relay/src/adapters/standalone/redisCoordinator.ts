import { isActiveCredential } from "../../core/auth.js";
import { RelayError, errorMessage } from "../../core/errors.js";
import type { RelayEnvelope } from "../../core/protocol.js";
import type {
  AttachClientArgs,
  AttachDaemonArgs,
  AttachResult,
  ClientAuthPrincipal,
  DaemonAuthPrincipal,
  RelayBroadcastResult,
  RelayCoordinator,
  RelayDeliveryResult,
  RelaySocket,
  RelayStore,
  RendezvousSnapshot,
} from "../../core/ports.js";

export const DEFAULT_REDIS_LEASE_TTL_MS = 15_000;
export const DEFAULT_REDIS_RENEW_INTERVAL_MS = 5_000;
export const DEFAULT_REDIS_REQUEST_TIMEOUT_MS = 1_000;
export const MIN_REDIS_LEASE_TTL_MS = 1_000;

export const REDIS_ATTACH_LEASE_SCRIPT = `
local lease_key = KEYS[1]
local epoch_key = KEYS[2]
local ttl_ms = tonumber(ARGV[1])
local min_epoch = tonumber(ARGV[2] or '0') or 0
local current_epoch = tonumber(redis.call('GET', epoch_key) or '0') or 0
if current_epoch < min_epoch then
  redis.call('SET', epoch_key, tostring(min_epoch))
  current_epoch = min_epoch
end
local current_lease = redis.call('GET', lease_key)
if current_lease then
  return {0, current_epoch, current_lease}
end
local epoch = tonumber(redis.call('INCR', epoch_key))
local payload = cjson.encode({
  daemon_id = ARGV[3],
  connection_id = ARGV[4],
  process_id = ARGV[5],
  owner_user_id = ARGV[6],
  credential_id = ARGV[7],
  lease_id = ARGV[8],
  issued_at_ms = tonumber(ARGV[9]),
  expires_at_ms = tonumber(ARGV[10]),
  epoch = epoch
})
redis.call('PSETEX', lease_key, ttl_ms, payload)
return {1, epoch, payload}
`;

export const REDIS_RENEW_LEASE_SCRIPT = `
local lease_key = KEYS[1]
local ttl_ms = tonumber(ARGV[7])
local current_lease = redis.call('GET', lease_key)
if not current_lease then
  return {0, ''}
end
local lease = cjson.decode(current_lease)
if lease.lease_id ~= ARGV[1]
  or lease.process_id ~= ARGV[2]
  or lease.connection_id ~= ARGV[3]
  or lease.owner_user_id ~= ARGV[4]
  or lease.credential_id ~= ARGV[5]
  or tonumber(lease.epoch) ~= tonumber(ARGV[6]) then
  return {0, current_lease}
end
lease.expires_at_ms = tonumber(ARGV[8])
local payload = cjson.encode(lease)
redis.call('PSETEX', lease_key, ttl_ms, payload)
return {1, payload}
`;

export const REDIS_RELEASE_LEASE_SCRIPT = `
local lease_key = KEYS[1]
local current_lease = redis.call('GET', lease_key)
if not current_lease then
  return {0, ''}
end
local lease = cjson.decode(current_lease)
if lease.lease_id ~= ARGV[1]
  or lease.process_id ~= ARGV[2]
  or lease.connection_id ~= ARGV[3]
  or lease.owner_user_id ~= ARGV[4]
  or lease.credential_id ~= ARGV[5]
  or tonumber(lease.epoch) ~= tonumber(ARGV[6]) then
  return {0, current_lease}
end
redis.call('DEL', lease_key)
return {1, current_lease}
`;

export interface RedisLikeClient {
  get(key: string): Promise<string | null> | string | null;
  set(key: string, value: string, options?: { PX?: number }): Promise<unknown> | unknown;
  del(key: string | string[]): Promise<number> | number;
  eval(script: string, options: { keys: string[]; arguments: string[] }): Promise<unknown> | unknown;
  publish(channel: string, message: string): Promise<number> | number;
  subscribe(channel: string, listener: (message: string, channel: string) => void): Promise<unknown> | unknown;
  unsubscribe(channel: string, listener?: (message: string, channel: string) => void): Promise<unknown> | unknown;
  sAdd(key: string, member: string): Promise<number> | number;
  sRem(key: string, member: string): Promise<number> | number;
  sMembers(key: string): Promise<string[]> | string[];
  pExpire(key: string, ttlMs: number): Promise<boolean | number> | boolean | number;
  pTTL(key: string): Promise<number> | number;
  quit?(): Promise<unknown> | unknown;
}

export interface RedisRelayCoordinatorOptions {
  store: RelayStore;
  commands: RedisLikeClient;
  publisher?: RedisLikeClient;
  subscriber?: RedisLikeClient;
  processId?: string;
  keyPrefix?: string;
  leaseTtlMs?: number;
  renewIntervalMs?: number;
  requestTimeoutMs?: number;
  nowMs?: () => number;
  disableAutoRenew?: boolean;
  closeClients?: () => Promise<void> | void;
}

type DaemonSlot = {
  daemonId: string;
  connectionId: string;
  socket: RelaySocket;
  epoch: number;
  lease: RedisLeasePayload;
  auth: DaemonAuthPrincipal;
};

type ClientSlot = {
  daemonId: string;
  clientId: string;
  connectionId: string;
  socket: RelaySocket;
  epoch: number;
  auth: ClientAuthPrincipal;
};

type RedisLeasePayload = {
  daemon_id: string;
  connection_id: string;
  process_id: string;
  owner_user_id: string;
  credential_id: string;
  epoch: number;
  lease_id: string;
  issued_at_ms: number;
  expires_at_ms: number;
};

type RedisClientPresence = {
  daemon_id: string;
  client_id: string;
  connection_id: string;
  process_id: string;
  owner_user_id: string;
  session_id: string;
  epoch: number;
  issued_at_ms: number;
  expires_at_ms: number;
};

type RouteRequest = {
  type: "send_to_daemon" | "broadcast_to_clients";
  requestId: string;
  replyTo: string;
  daemonId: string;
  epoch: number;
  connectionId?: string;
  connectionIds?: string[];
  envelope: RelayEnvelope;
};

type RouteReply = {
  type: "reply";
  requestId: string;
  delivery?: RelayDeliveryResult;
  broadcast?: RelayBroadcastResult;
  error?: string;
};

export class RedisRelayCoordinator implements RelayCoordinator {
  readonly kind = "redis-lease";
  readonly processId: string;
  readonly keyPrefix: string;
  readonly leaseTtlMs: number;
  readonly renewIntervalMs: number;
  readonly requestTimeoutMs: number;

  private readonly store: RelayStore;
  private readonly commands: RedisLikeClient;
  private readonly publisher: RedisLikeClient;
  private readonly subscriber: RedisLikeClient;
  private readonly nowMs: () => number;
  private readonly closeClients?: () => Promise<void> | void;
  private readonly disableAutoRenew: boolean;
  private readonly daemonsById = new Map<string, DaemonSlot>();
  private readonly clientsByConnectionId = new Map<string, ClientSlot>();
  private readonly clientConnectionsByDaemonId = new Map<string, Set<string>>();
  private readonly daemonRenewTimers = new Map<string, ReturnType<typeof setInterval>>();
  private readonly clientRenewTimers = new Map<string, ReturnType<typeof setInterval>>();
  private readonly pendingReplies = new Map<string, (reply: RouteReply) => void>();
  private readonly readyPromise: Promise<void>;
  private closed = false;

  constructor(options: RedisRelayCoordinatorOptions) {
    this.store = options.store;
    this.commands = options.commands;
    this.publisher = options.publisher || options.commands;
    this.subscriber = options.subscriber || options.commands;
    this.processId = cleanOptional(options.processId) || `relay-process-${crypto.randomUUID()}`;
    this.keyPrefix = cleanOptional(options.keyPrefix) || "torque:ee-relay:v1";
    this.leaseTtlMs = clampMs(options.leaseTtlMs, DEFAULT_REDIS_LEASE_TTL_MS, MIN_REDIS_LEASE_TTL_MS);
    this.renewIntervalMs = clampMs(
      options.renewIntervalMs,
      Math.max(1_000, Math.floor(this.leaseTtlMs / 3)),
      250,
    );
    this.requestTimeoutMs = clampMs(options.requestTimeoutMs, DEFAULT_REDIS_REQUEST_TIMEOUT_MS, 100);
    this.nowMs = options.nowMs || (() => Date.now());
    this.closeClients = options.closeClients;
    this.disableAutoRenew = Boolean(options.disableAutoRenew);
    this.readyPromise = this.subscribeForProcessMessages();
  }

  async attachDaemon(args: AttachDaemonArgs): Promise<AttachResult> {
    await this.ready();
    const daemonId = cleanRequired(args.daemonId, "daemonId");
    const socket = args.socket;
    const connectionId = cleanRequired(socket.id, "socket.id");
    const auth = daemonPrincipalOrLocalDev(daemonId, args.auth);
    const currentInstance = await this.store.getInstance(daemonId);
    if (currentInstance?.owner_user_id && currentInstance.owner_user_id !== auth.ownerUserId) {
      return { accepted: false, connectionId, epoch: await this.currentEpoch(daemonId), reason: "daemon_owner_mismatch" };
    }
    const credentialOk = await this.daemonCredentialStillActive(daemonId, auth);
    if (!credentialOk) {
      return { accepted: false, connectionId, epoch: await this.currentEpoch(daemonId), reason: "invalid_daemon_credential" };
    }

    const existingLease = await this.getLease(daemonId);
    if (existingLease && !await this.leaseStillAuthorized(existingLease)) {
      await this.releaseLease(existingLease);
    }

    const now = this.nowMs();
    const leaseId = crypto.randomUUID();
    const attach = await this.attachLease(daemonId, connectionId, auth, leaseId, now, Number(currentInstance?.fencing_epoch || 0));
    if (!attach.claimed || !attach.lease) {
      return {
        accepted: false,
        connectionId,
        epoch: attach.epoch,
        reason: "daemon_lease_active",
      };
    }

    const claimed = await this.store.claimInstanceOwner({
      id: daemonId,
      ownerUserId: auth.ownerUserId,
      credentialId: auth.credentialId,
      fencingEpoch: attach.lease.epoch,
      label: currentInstance?.label || daemonId,
      metadata: currentInstance?.metadata || { relay_coordination: "redis" },
    });
    if (!claimed.claimed) {
      await this.releaseLease(attach.lease);
      return {
        accepted: false,
        connectionId,
        epoch: attach.lease.epoch,
        reason: claimed.reason || "relay_instance_claim_failed",
      };
    }

    const slot: DaemonSlot = {
      daemonId,
      connectionId,
      socket,
      epoch: attach.lease.epoch,
      lease: attach.lease,
      auth,
    };
    this.daemonsById.set(daemonId, slot);
    this.startDaemonRenewal(slot);
    return { accepted: true, connectionId, epoch: slot.epoch };
  }

  async attachClient(args: AttachClientArgs): Promise<AttachResult> {
    await this.ready();
    const daemonId = cleanRequired(args.daemonId, "daemonId");
    const clientId = cleanRequired(args.clientId, "clientId");
    const socket = args.socket;
    const connectionId = cleanRequired(socket.id, "socket.id");
    const auth = clientPrincipalOrLocalDev(args.auth);
    const instance = await this.store.getInstance(daemonId);
    if (instance?.owner_user_id && instance.owner_user_id !== auth.ownerUserId) {
      return { accepted: false, connectionId, epoch: Number(instance.fencing_epoch || 0), reason: "client_owner_mismatch" };
    }
    const sessionOk = await this.clientSessionStillActive(auth, instance?.owner_user_id || auth.ownerUserId);
    if (!sessionOk) {
      return { accepted: false, connectionId, epoch: Number(instance?.fencing_epoch || 0), reason: "invalid_client_session" };
    }
    const lease = await this.getLease(daemonId);
    const epoch = lease?.epoch || Number(instance?.fencing_epoch || 0);
    const slot: ClientSlot = { daemonId, clientId, connectionId, socket, epoch, auth };
    this.clientsByConnectionId.set(connectionId, slot);
    const set = this.clientConnectionsByDaemonId.get(daemonId) || new Set<string>();
    set.add(connectionId);
    this.clientConnectionsByDaemonId.set(daemonId, set);
    await this.writeClientPresence(slot);
    this.startClientRenewal(slot);
    return { accepted: true, connectionId, epoch };
  }

  async detach(connectionId: string): Promise<void> {
    await this.ready();
    const id = cleanOptional(connectionId);
    if (!id) return;
    for (const [daemonId, slot] of this.daemonsById.entries()) {
      if (slot.connectionId !== id) continue;
      this.daemonsById.delete(daemonId);
      this.stopDaemonRenewal(daemonId);
      await this.releaseLease(slot.lease);
      return;
    }
    const client = this.clientsByConnectionId.get(id);
    if (!client) return;
    this.clientsByConnectionId.delete(id);
    const set = this.clientConnectionsByDaemonId.get(client.daemonId);
    if (set) {
      set.delete(id);
      if (set.size === 0) this.clientConnectionsByDaemonId.delete(client.daemonId);
    }
    this.stopClientRenewal(id);
    await this.removeClientPresence(client.daemonId, id);
  }

  async isCurrentDaemonConnection(daemonId: string, connectionId: string, epoch?: number): Promise<boolean> {
    await this.ready();
    const lease = await this.getLease(String(daemonId || "").trim());
    if (!lease) return false;
    if (lease.connection_id !== cleanOptional(connectionId)) return false;
    if (epoch !== undefined && Number(epoch || 0) !== Number(lease.epoch || 0)) return false;
    return this.leaseStillAuthorized(lease);
  }

  async sendToDaemon(daemonId: string, envelope: RelayEnvelope): Promise<RelayDeliveryResult> {
    await this.ready();
    const id = cleanRequired(daemonId, "daemonId");
    const lease = await this.getLease(id);
    if (!lease) return { delivered: false, reason: "daemon_offline", epoch: await this.currentEpoch(id) };
    if (!await this.leaseStillAuthorized(lease)) {
      await this.releaseLease(lease);
      return { delivered: false, reason: "daemon_owner_mismatch", epoch: lease.epoch };
    }
    if (lease.process_id === this.processId) {
      return this.sendToLocalDaemon(lease, envelope);
    }
    const reply = await this.requestRemoteProcess(lease.process_id, {
      type: "send_to_daemon",
      requestId: crypto.randomUUID(),
      replyTo: this.processChannel(),
      daemonId: id,
      epoch: lease.epoch,
      connectionId: lease.connection_id,
      envelope,
    });
    return reply.delivery || { delivered: false, reason: reply.error || "owner_process_unreachable", epoch: lease.epoch };
  }

  async broadcastToClients(daemonId: string, envelope: RelayEnvelope): Promise<RelayBroadcastResult> {
    await this.ready();
    const id = cleanRequired(daemonId, "daemonId");
    const lease = await this.getLease(id);
    const epoch = lease?.epoch || await this.currentEpoch(id);
    const presences = await this.listClientPresences(id);
    if (presences.length === 0) return { delivered: 0, connectionIds: [], epoch, reason: "no_clients" };

    const local: RedisClientPresence[] = [];
    const remote = new Map<string, string[]>();
    for (const presence of presences) {
      if (presence.process_id === this.processId) {
        local.push(presence);
      } else {
        const ids = remote.get(presence.process_id) || [];
        ids.push(presence.connection_id);
        remote.set(presence.process_id, ids);
      }
    }

    const connectionIds: string[] = [];
    let delivered = 0;
    const localResult = await this.broadcastToLocalClients(id, local.map((presence) => presence.connection_id), envelope, epoch);
    delivered += localResult.delivered;
    connectionIds.push(...localResult.connectionIds);

    for (const [processId, ids] of remote.entries()) {
      const reply = await this.requestRemoteProcess(processId, {
        type: "broadcast_to_clients",
        requestId: crypto.randomUUID(),
        replyTo: this.processChannel(),
        daemonId: id,
        epoch,
        connectionIds: ids,
        envelope,
      });
      if (reply.broadcast) {
        delivered += reply.broadcast.delivered;
        connectionIds.push(...reply.broadcast.connectionIds);
      }
    }
    return {
      delivered,
      connectionIds,
      epoch,
      reason: delivered ? undefined : "no_clients",
    };
  }

  async snapshot(daemonId: string): Promise<RendezvousSnapshot> {
    await this.ready();
    const id = cleanOptional(daemonId);
    const lease = id ? await this.getLease(id) : null;
    const leaseCurrent = lease ? await this.leaseStillAuthorized(lease) : false;
    if (lease && !leaseCurrent) await this.releaseLease(lease);
    const clients = id ? await this.listClientPresences(id) : [];
    return {
      daemon_id: id,
      daemon_online: Boolean(lease && leaseCurrent),
      daemon_connection_id: lease && leaseCurrent ? lease.connection_id : "",
      epoch: lease && leaseCurrent ? lease.epoch : await this.currentEpoch(id),
      client_connection_ids: clients.map((client) => client.connection_id),
      owner_user_id: lease && leaseCurrent ? lease.owner_user_id : "",
      daemon_credential_id: lease && leaseCurrent ? lease.credential_id : "",
    };
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    for (const timer of this.daemonRenewTimers.values()) clearInterval(timer);
    for (const timer of this.clientRenewTimers.values()) clearInterval(timer);
    this.daemonRenewTimers.clear();
    this.clientRenewTimers.clear();
    for (const [daemonId, slot] of this.daemonsById.entries()) {
      this.daemonsById.delete(daemonId);
      await this.releaseLease(slot.lease);
    }
    for (const client of Array.from(this.clientsByConnectionId.values())) {
      await this.removeClientPresence(client.daemonId, client.connectionId);
    }
    this.clientsByConnectionId.clear();
    await this.subscriber.unsubscribe(this.processChannel(), this.onProcessMessage);
    await this.closeClients?.();
  }

  private async ready(): Promise<void> {
    await this.readyPromise;
  }

  private async attachLease(
    daemonId: string,
    connectionId: string,
    auth: DaemonAuthPrincipal,
    leaseId: string,
    issuedAtMs: number,
    minEpoch: number,
  ): Promise<{ claimed: boolean; epoch: number; lease?: RedisLeasePayload; currentLease?: RedisLeasePayload }> {
    const expiresAtMs = issuedAtMs + this.leaseTtlMs;
    const result = await this.commands.eval(REDIS_ATTACH_LEASE_SCRIPT, {
      keys: [this.leaseKey(daemonId), this.epochKey(daemonId)],
      arguments: [
        String(this.leaseTtlMs),
        String(Math.max(0, Math.floor(Number(minEpoch || 0)))),
        daemonId,
        connectionId,
        this.processId,
        auth.ownerUserId,
        auth.credentialId,
        leaseId,
        String(issuedAtMs),
        String(expiresAtMs),
      ],
    });
    const [claimedRaw, epochRaw, payloadRaw] = arrayResult(result);
    const claimed = Number(claimedRaw || 0) === 1;
    const epoch = Number(epochRaw || 0);
    const payload = parseLease(String(payloadRaw || ""));
    return claimed
      ? { claimed, epoch, lease: payload || undefined }
      : { claimed, epoch, currentLease: payload || undefined };
  }

  private async renewLease(slot: DaemonSlot): Promise<boolean> {
    const lease = await this.getLease(slot.daemonId);
    if (!lease || !leaseMatchesSlot(lease, slot)) {
      await this.closeStaleDaemonSlot(slot, "stale_daemon_connection");
      return false;
    }
    if (!await this.leaseStillAuthorized(lease)) {
      await this.releaseLease(lease);
      await this.closeStaleDaemonSlot(slot, "authenticated_session_revoked");
      return false;
    }
    const expiresAtMs = this.nowMs() + this.leaseTtlMs;
    const result = await this.commands.eval(REDIS_RENEW_LEASE_SCRIPT, {
      keys: [this.leaseKey(slot.daemonId)],
      arguments: [
        slot.lease.lease_id,
        this.processId,
        slot.connectionId,
        slot.auth.ownerUserId,
        slot.auth.credentialId,
        String(slot.epoch),
        String(this.leaseTtlMs),
        String(expiresAtMs),
      ],
    });
    const [renewedRaw, payloadRaw] = arrayResult(result);
    const renewed = Number(renewedRaw || 0) === 1;
    if (!renewed) {
      await this.closeStaleDaemonSlot(slot, "stale_daemon_connection");
      return false;
    }
    const renewedLease = parseLease(String(payloadRaw || ""));
    if (renewedLease) slot.lease = renewedLease;
    return true;
  }

  private async releaseLease(lease: RedisLeasePayload): Promise<void> {
    await this.commands.eval(REDIS_RELEASE_LEASE_SCRIPT, {
      keys: [this.leaseKey(lease.daemon_id)],
      arguments: [
        lease.lease_id,
        lease.process_id,
        lease.connection_id,
        lease.owner_user_id,
        lease.credential_id,
        String(lease.epoch),
      ],
    });
  }

  private async getLease(daemonId: string): Promise<RedisLeasePayload | null> {
    const raw = await this.commands.get(this.leaseKey(daemonId));
    return parseLease(raw);
  }

  private async currentEpoch(daemonId: string): Promise<number> {
    if (!daemonId) return 0;
    const raw = await this.commands.get(this.epochKey(daemonId));
    const redisEpoch = Math.max(0, Math.floor(Number(raw || 0)));
    const instance = await this.store.getInstance(daemonId);
    return Math.max(redisEpoch, Number(instance?.fencing_epoch || 0));
  }

  private async leaseStillAuthorized(lease: RedisLeasePayload): Promise<boolean> {
    const instance = await this.store.getInstance(lease.daemon_id);
    if (!instance?.owner_user_id || instance.owner_user_id !== lease.owner_user_id) return false;
    if (Number(instance.fencing_epoch || 0) > Number(lease.epoch || 0)) return false;
    if (instance.active_credential_id && instance.active_credential_id !== lease.credential_id) return false;
    if (lease.credential_id === "local-dev-unauthenticated") return lease.owner_user_id === "local-dev";
    const credential = await this.store.getDaemonCredential(lease.daemon_id, lease.credential_id);
    return isActiveCredential(credential) && credential.owner_user_id === lease.owner_user_id;
  }

  private async daemonCredentialStillActive(daemonId: string, auth: DaemonAuthPrincipal): Promise<boolean> {
    if (auth.authMode === "local-dev-unauthenticated" || auth.credentialId === "local-dev-unauthenticated") {
      return auth.ownerUserId === "local-dev";
    }
    const credential = await this.store.getDaemonCredential(daemonId, auth.credentialId);
    return isActiveCredential(credential) && credential.owner_user_id === auth.ownerUserId;
  }

  private async clientSessionStillActive(auth: ClientAuthPrincipal, expectedOwnerUserId: string): Promise<boolean> {
    if (auth.authMode === "local-dev-unauthenticated" || auth.sessionId === "local-dev-unauthenticated") {
      return auth.ownerUserId === "local-dev";
    }
    const session = await this.store.getClientSession(auth.sessionId);
    const expires = Date.parse(session?.expires_at || "");
    return Boolean(
      session &&
      !session.revoked_at &&
      session.owner_user_id === expectedOwnerUserId &&
      Number.isFinite(expires) &&
      expires > Date.now()
    );
  }

  private async sendToLocalDaemon(lease: RedisLeasePayload, envelope: RelayEnvelope): Promise<RelayDeliveryResult> {
    if (!await this.isCurrentDaemonConnection(lease.daemon_id, lease.connection_id, lease.epoch)) {
      return { delivered: false, reason: "stale_daemon_connection", epoch: lease.epoch };
    }
    const slot = this.daemonsById.get(lease.daemon_id);
    if (!slot || slot.connectionId !== lease.connection_id || slot.epoch !== lease.epoch) {
      return { delivered: false, reason: "owner_process_unreachable", epoch: lease.epoch };
    }
    await slot.socket.sendEnvelope(envelope);
    return { delivered: true, connectionId: slot.connectionId, epoch: slot.epoch };
  }

  private async broadcastToLocalClients(
    daemonId: string,
    connectionIds: string[],
    envelope: RelayEnvelope,
    epoch: number,
  ): Promise<RelayBroadcastResult> {
    const deliveredIds: string[] = [];
    for (const connectionId of connectionIds) {
      const client = this.clientsByConnectionId.get(connectionId);
      if (!client || client.daemonId !== daemonId) continue;
      const instance = await this.store.getInstance(daemonId);
      if (instance?.owner_user_id && instance.owner_user_id !== client.auth.ownerUserId) {
        await this.detach(client.connectionId);
        void client.socket.close?.(4003, "authenticated_session_revoked");
        continue;
      }
      if (!await this.clientSessionStillActive(client.auth, instance?.owner_user_id || client.auth.ownerUserId)) {
        await this.detach(client.connectionId);
        void client.socket.close?.(4003, "authenticated_session_revoked");
        continue;
      }
      await client.socket.sendEnvelope(envelope);
      deliveredIds.push(connectionId);
    }
    return {
      delivered: deliveredIds.length,
      connectionIds: deliveredIds,
      epoch,
      reason: deliveredIds.length ? undefined : "no_clients",
    };
  }

  private async writeClientPresence(slot: ClientSlot): Promise<void> {
    const now = this.nowMs();
    const presence: RedisClientPresence = {
      daemon_id: slot.daemonId,
      client_id: slot.clientId,
      connection_id: slot.connectionId,
      process_id: this.processId,
      owner_user_id: slot.auth.ownerUserId,
      session_id: slot.auth.sessionId,
      epoch: slot.epoch,
      issued_at_ms: now,
      expires_at_ms: now + this.leaseTtlMs,
    };
    await this.commands.sAdd(this.clientsKey(slot.daemonId), slot.connectionId);
    await this.commands.set(this.clientKey(slot.daemonId, slot.connectionId), JSON.stringify(presence), { PX: this.leaseTtlMs });
  }

  private async removeClientPresence(daemonId: string, connectionId: string): Promise<void> {
    await this.commands.sRem(this.clientsKey(daemonId), connectionId);
    await this.commands.del(this.clientKey(daemonId, connectionId));
  }

  private async listClientPresences(daemonId: string): Promise<RedisClientPresence[]> {
    const ids = await this.commands.sMembers(this.clientsKey(daemonId));
    const presences: RedisClientPresence[] = [];
    for (const connectionId of ids) {
      const raw = await this.commands.get(this.clientKey(daemonId, connectionId));
      const presence = parseClientPresence(raw);
      if (!presence) {
        await this.commands.sRem(this.clientsKey(daemonId), connectionId);
        continue;
      }
      presences.push(presence);
    }
    return presences;
  }

  private startDaemonRenewal(slot: DaemonSlot): void {
    if (this.disableAutoRenew) return;
    const timer = setInterval(() => {
      void this.renewLease(slot).catch(() => {
        void this.closeStaleDaemonSlot(slot, "stale_daemon_connection");
      });
    }, this.renewIntervalMs);
    (timer as { unref?: () => void }).unref?.();
    this.daemonRenewTimers.set(slot.daemonId, timer);
  }

  private stopDaemonRenewal(daemonId: string): void {
    const timer = this.daemonRenewTimers.get(daemonId);
    if (timer) clearInterval(timer);
    this.daemonRenewTimers.delete(daemonId);
  }

  private startClientRenewal(slot: ClientSlot): void {
    if (this.disableAutoRenew) return;
    const timer = setInterval(() => {
      void this.writeClientPresence(slot).catch(() => undefined);
    }, this.renewIntervalMs);
    (timer as { unref?: () => void }).unref?.();
    this.clientRenewTimers.set(slot.connectionId, timer);
  }

  private stopClientRenewal(connectionId: string): void {
    const timer = this.clientRenewTimers.get(connectionId);
    if (timer) clearInterval(timer);
    this.clientRenewTimers.delete(connectionId);
  }

  private async closeStaleDaemonSlot(slot: DaemonSlot, reason: string): Promise<void> {
    if (this.daemonsById.get(slot.daemonId)?.connectionId === slot.connectionId) {
      this.daemonsById.delete(slot.daemonId);
      this.stopDaemonRenewal(slot.daemonId);
      void slot.socket.close?.(4001, reason);
    }
  }

  private async requestRemoteProcess(processId: string, request: RouteRequest): Promise<RouteReply> {
    const requestId = request.requestId;
    const promise = new Promise<RouteReply>((resolve) => {
      const timer = setTimeout(() => {
        this.pendingReplies.delete(requestId);
        resolve({ type: "reply", requestId, error: "owner_process_unreachable" });
      }, this.requestTimeoutMs);
      (timer as { unref?: () => void }).unref?.();
      this.pendingReplies.set(requestId, (reply) => {
        clearTimeout(timer);
        resolve(reply);
      });
    });
    await this.publisher.publish(this.processChannel(processId), JSON.stringify(request));
    return promise;
  }

  private async subscribeForProcessMessages(): Promise<void> {
    await this.subscriber.subscribe(this.processChannel(), this.onProcessMessage);
  }

  private readonly onProcessMessage = (message: string): void => {
    void this.handleProcessMessage(message).catch(() => undefined);
  };

  private async handleProcessMessage(message: string): Promise<void> {
    const parsed = parseRouteMessage(message);
    if (!parsed) return;
    if (parsed.type === "reply") {
      const resolver = this.pendingReplies.get(parsed.requestId);
      if (resolver) {
        this.pendingReplies.delete(parsed.requestId);
        resolver(parsed);
      }
      return;
    }
    if (parsed.type === "send_to_daemon") {
      const lease = await this.getLease(parsed.daemonId);
      const delivery = lease && lease.epoch === parsed.epoch && lease.connection_id === parsed.connectionId
        ? await this.sendToLocalDaemon(lease, parsed.envelope)
        : { delivered: false, reason: "stale_daemon_connection", epoch: parsed.epoch };
      await this.publisher.publish(parsed.replyTo, JSON.stringify({ type: "reply", requestId: parsed.requestId, delivery } satisfies RouteReply));
      return;
    }
    if (parsed.type === "broadcast_to_clients") {
      const broadcast = await this.broadcastToLocalClients(parsed.daemonId, parsed.connectionIds || [], parsed.envelope, parsed.epoch);
      await this.publisher.publish(parsed.replyTo, JSON.stringify({ type: "reply", requestId: parsed.requestId, broadcast } satisfies RouteReply));
    }
  }

  private leaseKey(daemonId: string): string {
    return `${this.daemonPrefix(daemonId)}:lease`;
  }

  private epochKey(daemonId: string): string {
    return `${this.daemonPrefix(daemonId)}:epoch`;
  }

  private clientsKey(daemonId: string): string {
    return `${this.daemonPrefix(daemonId)}:clients`;
  }

  private clientKey(daemonId: string, connectionId: string): string {
    return `${this.daemonPrefix(daemonId)}:client:${encodeURIComponent(connectionId)}`;
  }

  private daemonPrefix(daemonId: string): string {
    return `${this.keyPrefix}:{daemon:${encodeURIComponent(daemonId)}}`;
  }

  private processChannel(processId = this.processId): string {
    return `${this.keyPrefix}:process:${encodeURIComponent(processId)}`;
  }
}

export async function createRedisClientsFromUrl(url: string): Promise<{
  commands: RedisLikeClient;
  publisher: RedisLikeClient;
  subscriber: RedisLikeClient;
  close: () => Promise<void>;
}> {
  const redisUrl = cleanRequired(url, "redisUrl");
  try {
    const moduleName = "redis";
    const mod = await import(moduleName) as { createClient?: (options: Record<string, unknown>) => RedisLikeClient & { connect?: () => Promise<void>; duplicate?: () => RedisLikeClient & { connect?: () => Promise<void> } } };
    if (!mod.createClient) throw new Error("redis.createClient is unavailable");
    const clientOptions = { url: redisUrl, socket: { reconnectStrategy: false } };
    const commands = mod.createClient(clientOptions);
    await commands.connect?.();
    const publisher = commands.duplicate ? commands.duplicate() : mod.createClient(clientOptions);
    const subscriber = commands.duplicate ? commands.duplicate() : mod.createClient(clientOptions);
    await publisher.connect?.();
    await subscriber.connect?.();
    return {
      commands,
      publisher,
      subscriber,
      close: async () => {
        await Promise.allSettled([
          subscriber.quit?.(),
          publisher.quit?.(),
          commands.quit?.(),
        ]);
      },
    };
  } catch (error) {
    throw new RelayError(
      `Redis coordination requested but Redis client is unavailable or cannot connect: ${errorMessage(error)}`,
      "redis_connect_failed",
      false,
      error,
    );
  }
}

function daemonPrincipalOrLocalDev(daemonId: string, auth: DaemonAuthPrincipal | undefined): DaemonAuthPrincipal {
  return auth || {
    kind: "daemon",
    daemonId,
    ownerUserId: "local-dev",
    credentialId: "local-dev-unauthenticated",
    authMode: "local-dev-unauthenticated",
  };
}

function clientPrincipalOrLocalDev(auth: ClientAuthPrincipal | undefined): ClientAuthPrincipal {
  return auth || {
    kind: "client",
    ownerUserId: "local-dev",
    sessionId: "local-dev-unauthenticated",
    userId: "user",
    authMode: "local-dev-unauthenticated",
  };
}

function leaseMatchesSlot(lease: RedisLeasePayload, slot: DaemonSlot): boolean {
  return lease.daemon_id === slot.daemonId &&
    lease.connection_id === slot.connectionId &&
    lease.process_id === slot.lease.process_id &&
    lease.owner_user_id === slot.auth.ownerUserId &&
    lease.credential_id === slot.auth.credentialId &&
    Number(lease.epoch || 0) === Number(slot.epoch || 0) &&
    lease.lease_id === slot.lease.lease_id;
}

function parseLease(value: unknown): RedisLeasePayload | null {
  if (!value) return null;
  try {
    const raw = typeof value === "string" ? JSON.parse(value) : value as Record<string, unknown>;
    const lease = raw as Record<string, unknown>;
    const daemonId = cleanOptional(lease.daemon_id as string);
    const connectionId = cleanOptional(lease.connection_id as string);
    const processId = cleanOptional(lease.process_id as string);
    const ownerUserId = cleanOptional(lease.owner_user_id as string);
    const credentialId = cleanOptional(lease.credential_id as string);
    const leaseId = cleanOptional(lease.lease_id as string);
    const epoch = Math.max(0, Math.floor(Number(lease.epoch || 0)));
    if (!daemonId || !connectionId || !processId || !ownerUserId || !credentialId || !leaseId || !epoch) return null;
    return {
      daemon_id: daemonId,
      connection_id: connectionId,
      process_id: processId,
      owner_user_id: ownerUserId,
      credential_id: credentialId,
      epoch,
      lease_id: leaseId,
      issued_at_ms: Number(lease.issued_at_ms || 0),
      expires_at_ms: Number(lease.expires_at_ms || 0),
    };
  } catch {
    return null;
  }
}

function parseClientPresence(value: unknown): RedisClientPresence | null {
  if (!value) return null;
  try {
    const raw = typeof value === "string" ? JSON.parse(value) : value as Record<string, unknown>;
    const daemonId = cleanOptional(raw.daemon_id as string);
    const connectionId = cleanOptional(raw.connection_id as string);
    const processId = cleanOptional(raw.process_id as string);
    if (!daemonId || !connectionId || !processId) return null;
    return {
      daemon_id: daemonId,
      client_id: cleanOptional(raw.client_id as string),
      connection_id: connectionId,
      process_id: processId,
      owner_user_id: cleanOptional(raw.owner_user_id as string),
      session_id: cleanOptional(raw.session_id as string),
      epoch: Math.max(0, Math.floor(Number(raw.epoch || 0))),
      issued_at_ms: Number(raw.issued_at_ms || 0),
      expires_at_ms: Number(raw.expires_at_ms || 0),
    };
  } catch {
    return null;
  }
}

function parseRouteMessage(message: string): RouteRequest | RouteReply | null {
  try {
    const raw = JSON.parse(message) as Record<string, unknown>;
    if (raw.type === "reply" && raw.requestId) return raw as unknown as RouteReply;
    if ((raw.type === "send_to_daemon" || raw.type === "broadcast_to_clients") && raw.requestId && raw.replyTo && raw.daemonId && raw.envelope) {
      return raw as RouteRequest;
    }
    return null;
  } catch {
    return null;
  }
}

function arrayResult(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function cleanRequired(value: string, name: string): string {
  const text = cleanOptional(value);
  if (!text) throw new RelayError(`${name} is required`, "redis_coordinator_validation_error");
  return text;
}

function cleanOptional(value: unknown): string {
  return String(value || "").trim();
}

function clampMs(value: number | undefined, fallback: number, minimum: number): number {
  const numeric = Math.floor(Number(value || fallback));
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(minimum, numeric);
}

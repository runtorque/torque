import type { RelayEnvelope } from "../../core/protocol.js";
import type {
  AttachClientArgs,
  AttachDaemonArgs,
  AttachResult,
  RelayBroadcastResult,
  RelayCoordinator,
  RelayDeliveryResult,
  RelaySocket,
  RendezvousSnapshot,
} from "../../core/ports.js";

type DaemonSlot = {
  daemonId: string;
  connectionId: string;
  socket: RelaySocket;
  epoch: number;
};

type ClientSlot = {
  daemonId: string;
  clientId: string;
  connectionId: string;
  socket: RelaySocket;
  epoch: number;
};

export class StandaloneRegistryCoordinator implements RelayCoordinator {
  readonly kind = "standalone-registry";
  private readonly daemonsById = new Map<string, DaemonSlot>();
  private readonly clientsByConnectionId = new Map<string, ClientSlot>();
  private readonly clientConnectionsByDaemonId = new Map<string, Set<string>>();
  private readonly epochsByDaemonId = new Map<string, number>();

  attachDaemon(args: AttachDaemonArgs): AttachResult {
    const daemonId = cleanRequired(args.daemonId, "daemonId");
    const socket = args.socket;
    const connectionId = cleanRequired(socket.id, "socket.id");
    const previous = this.daemonsById.get(daemonId);
    const epoch = (this.epochsByDaemonId.get(daemonId) || 0) + 1;
    this.epochsByDaemonId.set(daemonId, epoch);
    this.daemonsById.set(daemonId, { daemonId, connectionId, socket, epoch });

    if (previous && previous.connectionId !== connectionId) {
      void previous.socket.close?.(4000, "replaced_by_new_daemon_connection");
    }

    return {
      accepted: true,
      connectionId,
      epoch,
      replacedConnectionId: previous && previous.connectionId !== connectionId
        ? previous.connectionId
        : undefined,
    };
  }

  attachClient(args: AttachClientArgs): AttachResult {
    const daemonId = cleanRequired(args.daemonId, "daemonId");
    const clientId = cleanRequired(args.clientId, "clientId");
    const socket = args.socket;
    const connectionId = cleanRequired(socket.id, "socket.id");
    const epoch = this.epochsByDaemonId.get(daemonId) || 0;
    this.clientsByConnectionId.set(connectionId, {
      daemonId,
      clientId,
      connectionId,
      socket,
      epoch,
    });
    const set = this.clientConnectionsByDaemonId.get(daemonId) || new Set<string>();
    set.add(connectionId);
    this.clientConnectionsByDaemonId.set(daemonId, set);
    return { accepted: true, connectionId, epoch };
  }

  detach(connectionId: string): void {
    const id = String(connectionId || "").trim();
    if (!id) return;
    for (const [daemonId, slot] of this.daemonsById.entries()) {
      if (slot.connectionId === id) {
        this.daemonsById.delete(daemonId);
        return;
      }
    }
    const client = this.clientsByConnectionId.get(id);
    if (!client) return;
    this.clientsByConnectionId.delete(id);
    const set = this.clientConnectionsByDaemonId.get(client.daemonId);
    if (set) {
      set.delete(id);
      if (set.size === 0) this.clientConnectionsByDaemonId.delete(client.daemonId);
    }
  }

  isCurrentDaemonConnection(daemonId: string, connectionId: string, epoch?: number): boolean {
    const slot = this.daemonsById.get(String(daemonId || "").trim());
    if (!slot) return false;
    if (slot.connectionId !== String(connectionId || "").trim()) return false;
    if (epoch !== undefined && Number(epoch || 0) !== slot.epoch) return false;
    return true;
  }

  async sendToDaemon(daemonId: string, envelope: RelayEnvelope): Promise<RelayDeliveryResult> {
    const slot = this.daemonsById.get(String(daemonId || "").trim());
    if (!slot) {
      return { delivered: false, reason: "daemon_offline", epoch: this.epochsByDaemonId.get(daemonId) || 0 };
    }
    await slot.socket.sendEnvelope(envelope);
    return { delivered: true, connectionId: slot.connectionId, epoch: slot.epoch };
  }

  async broadcastToClients(daemonId: string, envelope: RelayEnvelope): Promise<RelayBroadcastResult> {
    const id = String(daemonId || "").trim();
    const ids = this.clientConnectionsByDaemonId.get(id);
    if (!ids || ids.size === 0) {
      return { delivered: 0, connectionIds: [], epoch: this.epochsByDaemonId.get(id) || 0, reason: "no_clients" };
    }
    const connectionIds: string[] = [];
    for (const connectionId of Array.from(ids)) {
      const client = this.clientsByConnectionId.get(connectionId);
      if (!client) continue;
      await client.socket.sendEnvelope(envelope);
      connectionIds.push(connectionId);
    }
    return {
      delivered: connectionIds.length,
      connectionIds,
      epoch: this.epochsByDaemonId.get(id) || 0,
      reason: connectionIds.length ? undefined : "no_clients",
    };
  }

  snapshot(daemonId: string): RendezvousSnapshot {
    const id = String(daemonId || "").trim();
    const daemon = this.daemonsById.get(id);
    return {
      daemon_id: id,
      daemon_online: Boolean(daemon),
      daemon_connection_id: daemon?.connectionId || "",
      epoch: this.epochsByDaemonId.get(id) || 0,
      client_connection_ids: Array.from(this.clientConnectionsByDaemonId.get(id) || []),
    };
  }
}

function cleanRequired(value: string, name: string): string {
  const text = String(value || "").trim();
  if (!text) throw new Error(`${name} is required`);
  return text;
}

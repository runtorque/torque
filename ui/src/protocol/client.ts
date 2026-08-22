import {
  connectionActions,
  projectionActions,
  type AppStore,
} from '../app/store';
import type { TorqueCommand } from './commands';
import {
  isKnownDeltaOperation,
  parseServerFrameJson,
  type AuxiliaryFrame,
  type DeltaFrame,
  type StateFrame,
} from './types';

export interface WebSocketEventLike {
  data: unknown;
}

export interface WebSocketCloseEventLike {
  code?: number;
  reason?: string;
}

export interface WebSocketLike {
  readonly readyState: number;
  onopen: (() => void) | null;
  onclose: ((event: WebSocketCloseEventLike) => void) | null;
  onerror: (() => void) | null;
  onmessage: ((event: WebSocketEventLike) => void) | null;
  send(data: string): void;
  close(): void;
}

export interface ProtocolClientOptions {
  store: AppStore;
  url?: string;
  webSocketFactory?: (url: string) => WebSocketLike;
  reconnectDelayMs?: number;
  livenessCheckMs?: number;
  livenessStaleMs?: number;
  now?: () => number;
  setTimeoutFn?: typeof setTimeout;
  clearTimeoutFn?: typeof clearTimeout;
  setIntervalFn?: typeof setInterval;
  clearIntervalFn?: typeof clearInterval;
}

function randomClientId(): string {
  try {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  } catch {
    // Fall through to the deterministic shape used by older webviews.
  }
  return `client-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function defaultWebSocketUrl(clientId = randomClientId()): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = new URL(`${protocol}//${window.location.host}/ws`);
  url.searchParams.set('compact', '1');
  url.searchParams.set('client_id', clientId);
  return url.toString();
}

function frameString(data: unknown): string | null {
  if (typeof data === 'string') return data;
  if (data instanceof ArrayBuffer) return new TextDecoder().decode(data);
  return null;
}

export class TorqueProtocolClient {
  private readonly store: AppStore;
  private readonly url: string;
  private readonly webSocketFactory: (url: string) => WebSocketLike;
  private readonly reconnectDelayMs: number;
  private readonly livenessCheckMs: number;
  private readonly livenessStaleMs: number;
  private readonly now: () => number;
  private readonly setTimeoutFn: typeof setTimeout;
  private readonly clearTimeoutFn: typeof clearTimeout;
  private readonly setIntervalFn: typeof setInterval;
  private readonly clearIntervalFn: typeof clearInterval;
  private socket: WebSocketLike | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private livenessTimer: ReturnType<typeof setInterval> | null = null;
  private stopped = true;
  private hasConnected = false;
  private lastInboundAt = 0;

  constructor(options: ProtocolClientOptions) {
    this.store = options.store;
    this.url = options.url ?? defaultWebSocketUrl();
    this.webSocketFactory = options.webSocketFactory
      ?? ((url) => new WebSocket(url) as unknown as WebSocketLike);
    this.reconnectDelayMs = options.reconnectDelayMs ?? 2_000;
    this.livenessCheckMs = options.livenessCheckMs ?? 10_000;
    this.livenessStaleMs = options.livenessStaleMs ?? 30_000;
    this.now = options.now ?? Date.now;
    this.setTimeoutFn = options.setTimeoutFn
      ?? globalThis.setTimeout.bind(globalThis);
    this.clearTimeoutFn = options.clearTimeoutFn
      ?? globalThis.clearTimeout.bind(globalThis);
    this.setIntervalFn = options.setIntervalFn
      ?? globalThis.setInterval.bind(globalThis);
    this.clearIntervalFn = options.clearIntervalFn
      ?? globalThis.clearInterval.bind(globalThis);
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.hasConnected = false;
    this.clearReconnect();
    this.stopLivenessWatchdog();
    const socket = this.socket;
    this.socket = null;
    if (socket) socket.close();
  }

  sendCommand(command: TorqueCommand): boolean {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify(command));
    return true;
  }

  private connect(): void {
    if (this.stopped) return;
    this.clearReconnect();
    this.store.dispatch(connectionActions.connecting());
    const socket = this.webSocketFactory(this.url);
    this.socket = socket;

    socket.onopen = () => {
      if (socket !== this.socket || this.stopped) return;
      const at = this.now();
      this.lastInboundAt = at;
      this.store.dispatch(connectionActions.connected({ at, reconnect: this.hasConnected }));
      this.hasConnected = true;
      this.startLivenessWatchdog();
    };

    socket.onmessage = (event) => {
      if (socket !== this.socket || this.stopped) return;
      this.handleMessage(event);
    };

    socket.onerror = () => {
      if (socket === this.socket) socket.close();
    };

    socket.onclose = (event) => {
      if (socket !== this.socket) return;
      this.socket = null;
      this.stopLivenessWatchdog();
      const reason = event.reason || (event.code ? `WebSocket closed (${event.code})` : undefined);
      this.store.dispatch(connectionActions.disconnected({
        at: this.now(),
        ...(reason ? { reason } : {}),
      }));
      if (!this.stopped) {
        this.reconnectTimer = this.setTimeoutFn(() => this.connect(), this.reconnectDelayMs);
      }
    };
  }

  private handleMessage(event: WebSocketEventLike): void {
    const raw = frameString(event.data);
    const at = this.now();
    this.lastInboundAt = at;
    this.store.dispatch(connectionActions.inboundObserved(at));
    if (raw === null) {
      this.store.dispatch(connectionActions.protocolError({
        at,
        message: 'unsupported WebSocket frame payload',
      }));
      return;
    }

    const parsed = parseServerFrameJson(raw);
    if (!parsed.ok) {
      this.store.dispatch(connectionActions.protocolError({ at, message: parsed.error }));
      return;
    }

    if (parsed.frame.type === 'state') {
      this.acceptSnapshot(parsed.frame as StateFrame);
      return;
    }
    if (parsed.frame.type === 'delta') {
      this.acceptDelta(parsed.frame as DeltaFrame);
      return;
    }
    if (parsed.frame.type === 'focus_update') {
      this.store.dispatch(projectionActions.focusReceived(parsed.frame));
    }
    if (parsed.frame.type === 'task_detail') {
      this.store.dispatch(projectionActions.taskDetailReceived(parsed.frame));
    }
    this.store.dispatch(projectionActions.auxiliaryResourceReceived(parsed.frame));
    this.store.dispatch(connectionActions.auxiliaryFrameReceived(parsed.frame as AuxiliaryFrame));
  }

  private acceptSnapshot(frame: StateFrame): void {
    this.store.dispatch(projectionActions.snapshotReceived(frame));
    this.store.dispatch(connectionActions.snapshotAccepted(frame));
  }

  private acceptDelta(frame: DeltaFrame): void {
    const connection = this.store.getState().connection;
    if (connection.awaitingResync) return;

    if (frame.ops.some((operation) => !isKnownDeltaOperation(operation.op))) {
      const unknown = frame.ops
        .filter((operation) => !isKnownDeltaOperation(operation.op))
        .map((operation) => operation.op)
        .join(', ');
      this.requestResync(`unknown delta operation: ${unknown}`);
      return;
    }

    if (connection.expectedSeq === null || frame.seq !== connection.expectedSeq) {
      this.requestResync(
        `delta sequence gap: expected ${String(connection.expectedSeq)}, received ${frame.seq}`,
      );
      return;
    }

    this.store.dispatch(projectionActions.deltaReceived(frame));
    this.store.dispatch(connectionActions.deltaAccepted(frame));
  }

  private requestResync(reason: string): void {
    const state = this.store.getState().connection;
    if (state.awaitingResync) return;
    this.store.dispatch(connectionActions.resyncRequested({ at: this.now(), reason }));
    this.sendCommand({ cmd: 'resync', compact: 1 });
  }

  private startLivenessWatchdog(): void {
    this.stopLivenessWatchdog();
    this.livenessTimer = this.setIntervalFn(() => {
      if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
      if (!this.lastInboundAt) return;
      if (this.now() - this.lastInboundAt <= this.livenessStaleMs) return;
      this.socket.close();
    }, this.livenessCheckMs);
  }

  private stopLivenessWatchdog(): void {
    if (this.livenessTimer !== null) this.clearIntervalFn(this.livenessTimer);
    this.livenessTimer = null;
  }

  private clearReconnect(): void {
    if (this.reconnectTimer !== null) this.clearTimeoutFn(this.reconnectTimer);
    this.reconnectTimer = null;
  }
}

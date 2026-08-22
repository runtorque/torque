export type TerminalConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'unavailable';

interface DisposableLike { dispose(): void }

interface TerminalLike {
  cols: number;
  rows: number;
  loadAddon(addon: unknown): void;
  open(element: HTMLElement): void;
  onData(callback: (data: string) => void): DisposableLike;
  attachCustomKeyEventHandler?(callback: (event: KeyboardEvent) => boolean): void;
  focus(): void;
  paste(data: string): void;
  reset(): void;
  write(data: string, callback?: () => void): void;
  scrollToBottom(): void;
  dispose(): void;
}

interface FitAddonLike { fit(): void; dispose?(): void }

interface TerminalConstructor {
  new(options: Record<string, unknown>): TerminalLike;
}

interface FitAddonConstructor {
  new(): FitAddonLike;
}

declare global {
  interface Window {
    Terminal?: TerminalConstructor;
    FitAddon?: { FitAddon?: FitAddonConstructor };
  }
}

export interface WebSocketLike {
  readyState: number;
  onopen: (() => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  onmessage: ((event: MessageEvent<string>) => void) | null;
  send(data: string): void;
  close(): void;
}

export interface TerminalControllerOptions {
  cellId: string;
  sessionId: string;
  surface: HTMLElement;
  isActive: () => boolean;
  onStatus?: (status: TerminalConnectionStatus) => void;
  webSocketFactory?: (url: string) => WebSocketLike;
  reconnectDelayMs?: number;
  maxReconnects?: number;
  windowObject?: Window;
}

function terminalSocketUrl(cellId: string, targetWindow: Window): string {
  const protocol = targetWindow.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = new URL(`${protocol}//${targetWindow.location.host}/ws/terminal/${encodeURIComponent(cellId)}`);
  url.searchParams.set('client_id', `react-terminal-${targetWindow.crypto?.randomUUID?.() ?? Date.now()}`);
  return url.toString();
}

function surfaceIsVisible(surface: HTMLElement, targetWindow: Window): boolean {
  if (!surface.isConnected || targetWindow.document.visibilityState === 'hidden') return false;
  const bounds = surface.getBoundingClientRect();
  return bounds.width > 0 && bounds.height > 0;
}

export class TerminalController {
  private readonly options: TerminalControllerOptions;
  private readonly targetWindow: Window;
  private readonly terminal: TerminalLike;
  private readonly fitAddon: FitAddonLike;
  private readonly dataDisposable: DisposableLike;
  private readonly resizeObserver: ResizeObserver;
  private readonly webSocketFactory: (url: string) => WebSocketLike;
  private socket: WebSocketLike | null = null;
  private resizeFrame = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectCount = 0;
  private disposed = false;
  private lastColumns = 0;
  private lastRows = 0;

  constructor(options: TerminalControllerOptions) {
    this.options = options;
    this.targetWindow = options.windowObject ?? window;
    const TerminalClass = this.targetWindow.Terminal;
    const FitClass = this.targetWindow.FitAddon?.FitAddon;
    if (!TerminalClass || !FitClass) {
      options.onStatus?.('unavailable');
      throw new Error('The bundled xterm runtime is unavailable.');
    }
    this.webSocketFactory = options.webSocketFactory
      ?? ((url) => new WebSocket(url) as unknown as WebSocketLike);
    const terminalFontSize = Number.parseFloat(this.targetWindow.getComputedStyle(this.targetWindow.document.documentElement).getPropertyValue('--terminal-font-size')) || 12;
    this.terminal = new TerminalClass({
      cursorBlink: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
      fontSize: terminalFontSize,
      lineHeight: 1,
      scrollback: 5_000,
      convertEol: false,
      screenReaderMode: true,
      theme: {
        background: '#0d0f13',
        foreground: '#e5e8ee',
        cursor: '#8da2fb',
        selectionBackground: '#8da2fb4d',
      },
    });
    this.fitAddon = new FitClass();
    this.terminal.loadAddon(this.fitAddon);
    this.terminal.open(options.surface);
    this.dataDisposable = this.terminal.onData((data) => this.send({ type: 'input', data }));
    this.terminal.attachCustomKeyEventHandler?.((event) => {
      if (event.key === 'Enter' && event.shiftKey && !event.altKey && !event.ctrlKey && !event.metaKey) {
        if (event.type === 'keydown') this.send({ type: 'input', data: '\n' });
        return false;
      }
      return true;
    });
    this.resizeObserver = new ResizeObserver(() => this.scheduleFit());
    this.resizeObserver.observe(options.surface);
    this.targetWindow.document.addEventListener('visibilitychange', this.handleVisibility);
    this.connect();
    this.scheduleFit();
  }

  focus(): void {
    if (!this.canOwnPty()) return;
    this.terminal.focus();
    this.send({ type: 'focus' });
  }

  paste(text: string): void {
    if (!text) return;
    this.terminal.paste(text);
    this.focus();
  }

  scrollToTail(): void {
    this.terminal.scrollToBottom();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.options.onStatus?.('disconnected');
    this.targetWindow.document.removeEventListener('visibilitychange', this.handleVisibility);
    this.resizeObserver.disconnect();
    this.dataDisposable.dispose();
    if (this.resizeFrame) this.targetWindow.cancelAnimationFrame(this.resizeFrame);
    if (this.reconnectTimer) this.targetWindow.clearTimeout(this.reconnectTimer);
    this.socket?.close();
    this.socket = null;
    this.fitAddon.dispose?.();
    this.terminal.dispose();
  }

  private readonly handleVisibility = () => {
    if (this.canOwnPty()) {
      this.scheduleFit();
      this.focus();
    }
  };

  private canOwnPty(): boolean {
    return !this.disposed
      && this.options.isActive()
      && surfaceIsVisible(this.options.surface, this.targetWindow);
  }

  private connect(): void {
    if (this.disposed) return;
    this.options.onStatus?.('connecting');
    const socket = this.webSocketFactory(terminalSocketUrl(this.options.cellId, this.targetWindow));
    this.socket = socket;
    socket.onopen = () => {
      if (this.socket !== socket || this.disposed) return;
      this.reconnectCount = 0;
      this.options.onStatus?.('connected');
      this.scheduleFit();
    };
    socket.onmessage = (event) => {
      if (this.socket !== socket || this.disposed) return;
      let frame: Record<string, unknown>;
      try { frame = JSON.parse(String(event.data)) as Record<string, unknown>; } catch { return; }
      if (typeof frame.session_id === 'string' && frame.session_id !== this.options.sessionId) return;
      if (frame.type === 'snapshot') this.terminal.reset();
      if ((frame.type === 'snapshot' || frame.type === 'output') && typeof frame.data === 'string') {
        this.terminal.write(frame.data, () => {
          if (this.canOwnPty()) this.terminal.scrollToBottom();
        });
      }
      if (frame.type === 'error') this.options.onStatus?.('unavailable');
    };
    socket.onerror = () => socket.close();
    socket.onclose = () => {
      if (this.socket !== socket || this.disposed) return;
      this.socket = null;
      this.options.onStatus?.('disconnected');
      const maxReconnects = this.options.maxReconnects ?? 15;
      if (this.reconnectCount >= maxReconnects) return;
      this.reconnectCount += 1;
      this.reconnectTimer = this.targetWindow.setTimeout(
        () => this.connect(),
        this.options.reconnectDelayMs ?? 1_000,
      );
    };
  }

  private scheduleFit(): void {
    if (!this.canOwnPty() || this.resizeFrame) return;
    this.resizeFrame = this.targetWindow.requestAnimationFrame(() => {
      this.resizeFrame = 0;
      if (!this.canOwnPty()) return;
      this.fitAddon.fit();
      const columns = Math.max(1, this.terminal.cols);
      const rows = Math.max(1, this.terminal.rows);
      if (columns === this.lastColumns && rows === this.lastRows) return;
      this.lastColumns = columns;
      this.lastRows = rows;
      this.send({ type: 'resize', cols: columns, rows });
    });
  }

  private send(frame: Record<string, unknown>): void {
    if (!this.canOwnPty() || this.socket?.readyState !== 1) return;
    this.socket.send(JSON.stringify(frame));
  }
}

interface RegistryEntry {
  controller: TerminalController;
  leases: number;
  disposeTimer: ReturnType<typeof setTimeout> | null;
}

const controllers = new Map<string, RegistryEntry>();

export function acquireTerminalController(
  key: string,
  options: TerminalControllerOptions,
): { controller: TerminalController; release: () => void } {
  let entry = controllers.get(key);
  if (!entry) {
    entry = { controller: new TerminalController(options), leases: 0, disposeTimer: null };
    controllers.set(key, entry);
  }
  if (entry.disposeTimer) clearTimeout(entry.disposeTimer);
  entry.disposeTimer = null;
  entry.leases += 1;
  let released = false;
  return {
    controller: entry.controller,
    release: () => {
      if (released) return;
      released = true;
      const current = controllers.get(key);
      if (!current) return;
      current.leases = Math.max(0, current.leases - 1);
      if (current.leases) return;
      current.disposeTimer = setTimeout(() => {
        const latest = controllers.get(key);
        if (!latest || latest.leases) return;
        latest.controller.dispose();
        controllers.delete(key);
      }, 0);
    },
  };
}

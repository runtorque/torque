import { terminalAppearance, terminalScrollback } from './terminalPreferences';

export type TerminalConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'unavailable';

interface DisposableLike { dispose(): void }

interface TerminalLike {
  options: { fontSize?: number; scrollback?: number; theme?: Record<string, string> };
  cols: number;
  rows: number;
  buffer?: { active: { baseY: number; viewportY: number; type?: string } };
  modes?: { mouseTrackingMode: string };
  loadAddon(addon: unknown): void;
  open(element: HTMLElement): void;
  onScroll?(callback: () => void): DisposableLike;
  onData(callback: (data: string) => void): DisposableLike;
  attachCustomWheelEventHandler?(callback: (event: WheelEvent) => boolean): void;
  attachCustomKeyEventHandler?(callback: (event: KeyboardEvent) => boolean): void;
  focus(): void;
  paste(data: string): void;
  reset(): void;
  write(data: string, callback?: () => void): void;
  scrollToBottom(): void;
  scrollToLine?(line: number): void;
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
  scrollback?: unknown;
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
  private readonly scrollDisposable: DisposableLike | undefined;
  private tailPinned = true;
  private scrollIntentUntil = 0;
  private scrollPointerDown = false;
  private readonly resizeObserver: ResizeObserver;
  private readonly appearanceObserver: MutationObserver;
  private appearance: ReturnType<typeof terminalAppearance>;
  private readonly webSocketFactory: (url: string) => WebSocketLike;
  private socket: WebSocketLike | null = null;
  private resizeFrame = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectCount = 0;
  private disposed = false;
  private wheelRemainder = 0;
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
    this.appearance = terminalAppearance(this.targetWindow);
    this.terminal = new TerminalClass({
      cursorBlink: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
      ...this.appearance,
      lineHeight: 1,
      scrollback: terminalScrollback(options.scrollback),
      convertEol: false,
      screenReaderMode: true,
    });
    this.fitAddon = new FitClass();
    this.terminal.loadAddon(this.fitAddon);
    this.terminal.open(options.surface);
    const initialBuffer = this.terminal.buffer?.active;
    this.tailPinned = !initialBuffer || initialBuffer.viewportY === initialBuffer.baseY;
    this.scrollDisposable = this.terminal.onScroll?.(() => {
      const buffer = this.terminal.buffer?.active;
      if (!buffer) return;
      if (buffer.viewportY === buffer.baseY) this.tailPinned = true;
      else if (this.scrollPointerDown || Date.now() < this.scrollIntentUntil) this.tailPinned = false;
    });
    options.surface.addEventListener('pointerdown', this.handleScrollPointerDown, true);
    options.surface.addEventListener('touchstart', this.handleScrollIntent, { passive: true });
    this.targetWindow.document.addEventListener('pointerup', this.handleScrollPointerUp);
    this.targetWindow.document.addEventListener('pointercancel', this.handleScrollPointerUp);
    this.dataDisposable = this.terminal.onData((data) => this.send({ type: 'input', data }));
    this.terminal.attachCustomKeyEventHandler?.((event) => {
      if (['PageUp', 'PageDown', 'Home', 'End'].includes(event.key)) this.handleScrollIntent();
      if (event.key === 'Enter' && event.shiftKey && !event.altKey && !event.ctrlKey && !event.metaKey) {
        if (event.type === 'keydown') this.send({ type: 'input', data: '\n' });
        return false;
      }
      return true;
    });
    this.terminal.attachCustomWheelEventHandler?.((event) => {
      const buffer = this.terminal.buffer?.active;
      if (!buffer || buffer.type === 'alternate' || !buffer.baseY || !this.terminal.scrollToLine
        || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey
        || (this.terminal.modes && this.terminal.modes.mouseTrackingMode !== 'none')) return true;
      const screen = options.surface.querySelector('.xterm-screen');
      const rowHeight = (screen?.getBoundingClientRect().height || 0) / this.terminal.rows;
      if (!rowHeight || !event.deltaY) return true;
      // Update the public buffer position synchronously. The bundled xterm's
      // DOM scroll suppression can otherwise consume a wheel event when an
      // output-driven scrollTop write and user input land in the same frame.
      this.wheelRemainder += event.deltaY * (event.deltaMode === 1 ? 1 : event.deltaMode === 2 ? this.terminal.rows : 1 / rowHeight);
      const lines = Math.trunc(this.wheelRemainder);
      this.wheelRemainder -= lines;
      const nextLine = Math.max(0, Math.min(buffer.baseY, buffer.viewportY + lines));
      this.tailPinned = nextLine === buffer.baseY;
      this.terminal.scrollToLine(nextLine);
      event.preventDefault();
      return false;
    });
    this.resizeObserver = new ResizeObserver(() => this.scheduleFit());
    this.resizeObserver.observe(options.surface);
    this.appearanceObserver = new MutationObserver(this.refreshAppearance);
    this.appearanceObserver.observe(this.targetWindow.document.documentElement, {
      attributes: true, attributeFilter: ['style', 'data-torque-contrast'],
    });
    this.targetWindow.document.addEventListener('visibilitychange', this.handleVisibility);
    this.connect();
    this.scheduleFit();
  }

  setScrollback(value: unknown): void {
    if (this.disposed) return;
    const scrollback = terminalScrollback(value);
    if (this.terminal.options.scrollback !== scrollback) this.terminal.options.scrollback = scrollback;
  }

  private readonly refreshAppearance = () => {
    if (this.disposed) return;
    const next = terminalAppearance(this.targetWindow);
    const fontChanged = next.fontSize !== this.appearance.fontSize;
    const themeChanged = (Object.keys(next.theme) as (keyof typeof next.theme)[]).some((key) => next.theme[key] !== this.appearance.theme[key]);
    this.appearance = next;
    if (fontChanged) this.terminal.options.fontSize = next.fontSize;
    if (themeChanged) this.terminal.options.theme = { ...next.theme };
    // Color-only changes need no PTY resize. The existing fit path preserves
    // reading/tail intent and sends geometry only for the visible owner.
    if (fontChanged) this.scheduleFit();
  };

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
    this.tailPinned = true;
    this.scrollIntentUntil = 0;
    this.terminal.scrollToBottom();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.options.onStatus?.('disconnected');
    this.targetWindow.document.removeEventListener('visibilitychange', this.handleVisibility);
    this.resizeObserver.disconnect();
    this.appearanceObserver.disconnect();
    this.dataDisposable.dispose();
    this.scrollDisposable?.dispose();
    this.options.surface.removeEventListener('pointerdown', this.handleScrollPointerDown, true);
    this.options.surface.removeEventListener('touchstart', this.handleScrollIntent);
    this.targetWindow.document.removeEventListener('pointerup', this.handleScrollPointerUp);
    this.targetWindow.document.removeEventListener('pointercancel', this.handleScrollPointerUp);
    if (this.resizeFrame) this.targetWindow.cancelAnimationFrame(this.resizeFrame);
    if (this.reconnectTimer) this.targetWindow.clearTimeout(this.reconnectTimer);
    this.socket?.close();
    this.socket = null;
    this.fitAddon.dispose?.();
    this.terminal.dispose();
  }

  private readonly handleScrollIntent = () => { this.scrollIntentUntil = Date.now() + 500; };
  private readonly handleScrollPointerDown = () => { this.scrollPointerDown = true; this.handleScrollIntent(); };
  private readonly handleScrollPointerUp = () => { this.scrollPointerDown = false; };

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
      this.lastColumns = 0;
      this.lastRows = 0;
      this.scheduleFit();
    };
    socket.onmessage = (event) => {
      if (this.socket !== socket || this.disposed) return;
      let frame: Record<string, unknown>;
      try { frame = JSON.parse(String(event.data)) as Record<string, unknown>; } catch { return; }
      if (typeof frame.session_id === 'string' && frame.session_id !== this.options.sessionId) return;
      if (frame.type === 'snapshot' && typeof frame.data === 'string') {
        const buffer = this.terminal.buffer?.active;
        const distanceFromTail = !this.tailPinned && buffer ? Math.max(0, buffer.baseY - buffer.viewportY) : 0;
        this.terminal.reset();
        this.terminal.write(frame.data, () => {
          if (this.disposed) return;
          this.tailPinned = distanceFromTail === 0;
          if (distanceFromTail > 0 && this.terminal.buffer) {
            this.terminal.scrollToLine?.(Math.max(0, this.terminal.buffer.active.baseY - distanceFromTail));
          } else if (this.canOwnPty()) this.terminal.scrollToBottom();
        });
      } else if (frame.type === 'output' && typeof frame.data === 'string') {
        this.terminal.write(frame.data, () => {
          // A delayed DOM scroll after fit can move xterm off the tail without
          // operator input. Follow the recorded intent, not that transient row.
          if (this.tailPinned && this.canOwnPty()) this.terminal.scrollToBottom();
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
      const buffer = this.terminal.buffer?.active;
      const distanceFromTail = !this.tailPinned && buffer ? Math.max(0, buffer.baseY - buffer.viewportY) : 0;
      this.fitAddon.fit();
      this.tailPinned = distanceFromTail === 0;
      if (distanceFromTail > 0 && this.terminal.buffer) {
        this.terminal.scrollToLine?.(Math.max(0, this.terminal.buffer.active.baseY - distanceFromTail));
      } else this.terminal.scrollToBottom();
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

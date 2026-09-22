import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  acquireTerminalController,
  TerminalController,
  type WebSocketLike,
} from './terminalController';

const writes: string[] = [];
const sent: string[] = [];
let terminalDisposals = 0;
let socketCreations = 0;
let terminalOptions: Record<string, unknown> = {};
const sockets: WebSocketLike[] = [];
let tailCalls = 0;

class FakeTerminal {
  static current: FakeTerminal;
  constructor(options: Record<string, unknown>) { terminalOptions = options; FakeTerminal.current = this; }
  buffer = { active: { baseY: 100, viewportY: 60 } };
  cols = 100;
  rows = 30;
  loadAddon() {}
  open() {}
  onData() { return { dispose() {} }; }
  wheel?: (event: WheelEvent) => boolean;
  modes = { mouseTrackingMode: 'none' };
  attachCustomWheelEventHandler(callback: (event: WheelEvent) => boolean) { this.wheel = callback; }
  attachCustomKeyEventHandler() {}
  focus() {}
  paste() {}
  reset() { writes.push('reset'); }
  write(data: string, callback?: () => void) { writes.push(data); callback?.(); }
  scrollToBottom() { tailCalls += 1; this.buffer.active.viewportY = this.buffer.active.baseY; }
  scrollToLine(line: number) { this.buffer.active.viewportY = line; }
  dispose() { terminalDisposals += 1; }
}

class FakeFitAddon {
  fit() {}
  dispose() {}
}

class FakeResizeObserver implements ResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe() { this.callback([], this); }
  disconnect() {}
  unobserve() {}
  takeRecords() { return []; }
}

function socketFactory(): WebSocketLike {
  socketCreations += 1;
  const socket: WebSocketLike = {
    readyState: 1,
    onopen: null,
    onclose: null,
    onerror: null,
    onmessage: null,
    send: (data) => sent.push(data),
    close() {},
  };
  sockets.push(socket);
  return socket;
}

function surface() {
  const element = document.createElement('div');
  element.getBoundingClientRect = () => ({ width: 800, height: 400, x: 0, y: 0, top: 0, right: 800, bottom: 400, left: 0, toJSON() {} });
  document.body.append(element);
  return element;
}

beforeEach(() => {
  writes.length = 0;
  tailCalls = 0;
  sent.length = 0;
  terminalDisposals = 0;
  socketCreations = 0;
  terminalOptions = {};
  sockets.length = 0;
  vi.useFakeTimers();
  window.Terminal = FakeTerminal;
  window.FitAddon = { FitAddon: FakeFitAddon };
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
});

afterEach(() => {
  vi.runAllTimers();
  vi.useRealTimers();
  delete window.Terminal;
  delete window.FitAddon;
  vi.unstubAllGlobals();
});

describe('terminal controller', () => {
  it('writes PTY frames directly to xterm without application state', () => {
    const target = surface();
    const controller = new TerminalController({
      cellId: 'worker-1',
      sessionId: 'session-1',
      surface: target,
      isActive: () => true,
      webSocketFactory: socketFactory,
    });
    const socket = (controller as unknown as { socket: WebSocketLike }).socket;
    socket.onopen?.();
    socket.onmessage?.({ data: JSON.stringify({ type: 'snapshot', session_id: 'session-1', data: 'hello' }) } as MessageEvent<string>);
    socket.onmessage?.({ data: JSON.stringify({ type: 'output', session_id: 'session-1', data: ' world' }) } as MessageEvent<string>);

    expect(writes).toEqual(['reset', 'hello', ' world']);
    expect(terminalOptions.screenReaderMode).toBe(true);
    controller.dispose();
    expect(terminalDisposals).toBe(1);
  });

  it('keeps scrollback reading in place under live output and only tails explicitly', () => {
    const controller = new TerminalController({
      cellId: 'reader', sessionId: 'reader-session', surface: surface(),
      isActive: () => true, webSocketFactory: socketFactory,
    });
    sockets[0]?.onmessage?.({ data: JSON.stringify({ type: 'output', session_id: 'reader-session', data: 'new output' }) } as MessageEvent<string>);
    expect(FakeTerminal.current.buffer.active.viewportY).toBe(60);
    expect(tailCalls).toBe(0);
    controller.scrollToTail();
    expect(FakeTerminal.current.buffer.active.viewportY).toBe(100);
    controller.dispose();
  });

  it('preserves the scrollback distance when a reconnect snapshot replaces the buffer', () => {
    const controller = new TerminalController({
      cellId: 'reader', sessionId: 'reader-session', surface: surface(),
      isActive: () => true, webSocketFactory: socketFactory,
    });
    vi.spyOn(FakeTerminal.current, 'reset').mockImplementation(() => { FakeTerminal.current.buffer.active.baseY = 0; FakeTerminal.current.buffer.active.viewportY = 0; });
    vi.spyOn(FakeTerminal.current, 'write').mockImplementation((_data, callback) => { FakeTerminal.current.buffer.active.baseY = 130; callback?.(); });
    sockets[0]?.onmessage?.({ data: JSON.stringify({ type: 'snapshot', session_id: 'reader-session', data: 'replayed output' }) } as MessageEvent<string>);
    expect(FakeTerminal.current.buffer.active.viewportY).toBe(90);
    controller.dispose();
  });

  it('keeps a tail-pinned viewport pinned when fitting changes its scroll position', () => {
    const controller = new TerminalController({
      cellId: 'owner', sessionId: 'owner-session', surface: surface(),
      isActive: () => true, webSocketFactory: socketFactory,
    });
    FakeTerminal.current.buffer.active.viewportY = 100;
    const fit = vi.spyOn(FakeFitAddon.prototype, 'fit').mockImplementation(() => {
      FakeTerminal.current.buffer.active.viewportY = 90;
    });
    sockets[0]?.onopen?.(); vi.advanceTimersByTime(20);
    expect(FakeTerminal.current.buffer.active.viewportY).toBe(100);
    fit.mockRestore();
    controller.dispose();
  });

  it('restores the owning surface dimensions after reconnect even when its geometry is unchanged', () => {
    const controller = new TerminalController({
      cellId: 'owner', sessionId: 'owner-session', surface: surface(),
      isActive: () => true, webSocketFactory: socketFactory, reconnectDelayMs: 25,
    });
    sockets[0]?.onopen?.(); vi.advanceTimersByTime(20);
    expect(sent.map((frame) => JSON.parse(frame) as { type: string }).filter((frame) => frame.type === 'resize')).toHaveLength(1);
    sockets[0]?.onclose?.(); vi.advanceTimersByTime(25);
    sockets[1]?.onopen?.(); vi.advanceTimersByTime(20);
    expect(sent.map((frame) => JSON.parse(frame) as { type: string }).filter((frame) => frame.type === 'resize')).toHaveLength(2);
    controller.dispose();
  });

  it('applies wheel intent to scrollback before output can overwrite the DOM scroll position', () => {
    const target = surface();
    const screen = document.createElement('div'); screen.className = 'xterm-screen';
    screen.getBoundingClientRect = () => ({ height: 420 } as DOMRect);
    target.append(screen);
    const controller = new TerminalController({ cellId: 'wheel', sessionId: 's', surface: target, isActive: () => true, webSocketFactory: socketFactory });
    const terminal = FakeTerminal.current;
    const event = new WheelEvent('wheel', { deltaY: -140, cancelable: true });
    expect(terminal.wheel?.(event)).toBe(false);
    expect(event.defaultPrevented).toBe(true);
    expect(terminal.buffer.active.viewportY).toBe(50);
    sockets[0]?.onmessage?.({ data: JSON.stringify({ type: 'output', data: 'tick' }) } as MessageEvent<string>);
    expect(terminal.buffer.active.viewportY).toBe(50);
    terminal.wheel?.(new WheelEvent('wheel', { deltaY: 1, deltaMode: 1 }));
    expect(terminal.buffer.active.viewportY).toBe(51);
    terminal.wheel?.(new WheelEvent('wheel', { deltaY: -1, deltaMode: 2 }));
    expect(terminal.buffer.active.viewportY).toBe(21);
    terminal.modes.mouseTrackingMode = 'vt200';
    expect(terminal.wheel?.(event)).toBe(true);
    expect(terminal.buffer.active.viewportY).toBe(21);
    terminal.modes.mouseTrackingMode = 'none';
    expect(terminal.wheel?.(new WheelEvent('wheel', { deltaY: -140, ctrlKey: true }))).toBe(true);
    controller.dispose();
  });

  it('shares one controller across Strict Mode lease churn', () => {
    const target = surface();
    const options = {
      cellId: 'worker-2',
      sessionId: 'session-2',
      surface: target,
      isActive: () => true,
      webSocketFactory: socketFactory,
    };
    const first = acquireTerminalController('main:worker-2:session-2', options);
    first.release();
    const second = acquireTerminalController('main:worker-2:session-2', options);

    expect(socketCreations).toBe(1);
    vi.runAllTimers();
    expect(terminalDisposals).toBe(0);
    second.release();
    vi.runAllTimers();
    expect(terminalDisposals).toBe(1);
  });

  it('reconnects the same terminal after a daemon-side socket close', () => {
    const controller = new TerminalController({
      cellId: 'worker-3',
      sessionId: 'session-3',
      surface: surface(),
      isActive: () => true,
      webSocketFactory: socketFactory,
      reconnectDelayMs: 25,
    });
    sockets[0]?.onopen?.();
    sockets[0]?.onclose?.();
    vi.advanceTimersByTime(25);
    expect(socketCreations).toBe(2);
    sockets[1]?.onopen?.();
    sockets[1]?.onmessage?.({ data: JSON.stringify({ type: 'output', session_id: 'session-3', data: 'restored' }) } as MessageEvent<string>);
    expect(writes).toContain('restored');
    controller.dispose();
  });

  it('never emits focus or resize frames from a hidden surface', () => {
    const hidden = surface();
    hidden.getBoundingClientRect = () => ({ width: 0, height: 0, x: 0, y: 0, top: 0, right: 0, bottom: 0, left: 0, toJSON() {} });
    const controller = new TerminalController({
      cellId: 'worker-4',
      sessionId: 'session-4',
      surface: hidden,
      isActive: () => true,
      webSocketFactory: socketFactory,
    });
    sockets[0]?.onopen?.();
    controller.focus();
    vi.runAllTimers();
    expect(sent).toEqual([]);
    controller.dispose();
  });

  it('sustains a representative burst of terminal output outside React state', () => {
    const controller = new TerminalController({
      cellId: 'worker-throughput',
      sessionId: 'session-throughput',
      surface: surface(),
      isActive: () => true,
      webSocketFactory: socketFactory,
    });
    const socket = sockets[0];
    socket?.onopen?.();
    const started = performance.now();
    for (let index = 0; index < 10_000; index += 1) {
      socket?.onmessage?.({
        data: JSON.stringify({ type: 'output', session_id: 'session-throughput', data: `line ${index}\n` }),
      } as MessageEvent<string>);
    }
    const elapsed = performance.now() - started;

    expect(writes).toHaveLength(10_000);
    expect(elapsed).toBeLessThan(3_000);
    expect(sent).toEqual([]);
    controller.dispose();
  });
});

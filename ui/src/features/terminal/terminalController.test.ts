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

class FakeTerminal {
  constructor(options: Record<string, unknown>) { terminalOptions = options; }
  cols = 100;
  rows = 30;
  loadAddon() {}
  open() {}
  onData() { return { dispose() {} }; }
  attachCustomKeyEventHandler() {}
  focus() {}
  paste() {}
  reset() { writes.push('reset'); }
  write(data: string, callback?: () => void) { writes.push(data); callback?.(); }
  scrollToBottom() {}
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

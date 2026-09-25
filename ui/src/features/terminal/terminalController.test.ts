import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  acquireTerminalController,
  TerminalController,
  type WebSocketLike,
} from './terminalController';
import { applyAppearance, appearanceDefaults } from '../../app/preferences';

const writes: string[] = [];
const sent: string[] = [];
let terminalDisposals = 0;
let socketCreations = 0;
let terminalOptions: Record<string, unknown> = {};
const sockets: WebSocketLike[] = [];
let tailCalls = 0;

class FakeTerminal {
  static current: FakeTerminal;
  constructor(public options: Record<string, unknown>) { terminalOptions = options; FakeTerminal.current = this; }
  buffer = { active: { baseY: 100, viewportY: 60 } };
  cols = 100;
  rows = 30;
  loadAddon() {}
  open() {}
  scrolled?: () => void;
  onScroll(callback: () => void) { this.scrolled = callback; return { dispose() {} }; }
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
  document.documentElement.removeAttribute('style');
  document.documentElement.removeAttribute('data-torque-contrast');
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
  vi.restoreAllMocks();
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
    vi.spyOn(FakeTerminal.current, 'reset').mockImplementation(() => { FakeTerminal.current.buffer.active.baseY = 0; FakeTerminal.current.buffer.active.viewportY = 0; FakeTerminal.current.scrolled?.(); });
    vi.spyOn(FakeTerminal.current, 'write').mockImplementation((_data, callback) => { FakeTerminal.current.buffer.active.baseY = 130; callback?.(); });
    sockets[0]?.onmessage?.({ data: JSON.stringify({ type: 'snapshot', session_id: 'reader-session', data: 'replayed output' }) } as MessageEvent<string>);
    expect(FakeTerminal.current.buffer.active.viewportY).toBe(90);
    sockets[0]?.onmessage?.({ data: JSON.stringify({ type: 'output', data: 'resumed output' }) } as MessageEvent<string>);
    expect(FakeTerminal.current.buffer.active.viewportY).toBe(90);
    controller.dispose();
  });

  it('keeps a tail-pinned viewport pinned when fitting changes its scroll position', () => {
    const controller = new TerminalController({
      cellId: 'owner', sessionId: 'owner-session', surface: surface(),
      isActive: () => true, webSocketFactory: socketFactory,
    });
    controller.scrollToTail();
    const fit = vi.spyOn(FakeFitAddon.prototype, 'fit').mockImplementation(() => {
      FakeTerminal.current.buffer.active.viewportY = 90;
    });
    sockets[0]?.onopen?.(); vi.advanceTimersByTime(20);
    expect(FakeTerminal.current.buffer.active.viewportY).toBe(100);
    fit.mockRestore();
    controller.dispose();
  });

  it('honors scrollbar reading intent and resumes when the operator returns to the tail', () => {
    const target = surface();
    const controller = new TerminalController({ cellId: 'scrollbar', sessionId: 's', surface: target, isActive: () => true, webSocketFactory: socketFactory });
    controller.scrollToTail();
    target.dispatchEvent(new Event('pointerdown'));
    FakeTerminal.current.buffer.active.viewportY = 50; FakeTerminal.current.scrolled?.();
    document.dispatchEvent(new Event('pointerup'));
    sockets[0]?.onmessage?.({ data: JSON.stringify({ type: 'output', data: 'tick' }) } as MessageEvent<string>);
    expect(FakeTerminal.current.buffer.active.viewportY).toBe(50);
    FakeTerminal.current.buffer.active.viewportY = 100; FakeTerminal.current.scrolled?.();
    vi.advanceTimersByTime(600);
    FakeTerminal.current.buffer.active.viewportY = 90; FakeTerminal.current.scrolled?.();
    sockets[0]?.onmessage?.({ data: JSON.stringify({ type: 'output', data: 'tick' }) } as MessageEvent<string>);
    expect(FakeTerminal.current.buffer.active.viewportY).toBe(100);
    controller.dispose();
  });

  it('continues following after a delayed viewport event following fit', () => {
    const controller = new TerminalController({ cellId: 'fit', sessionId: 's', surface: surface(), isActive: () => true, webSocketFactory: socketFactory });
    controller.scrollToTail();
    sockets[0]?.onopen?.(); vi.advanceTimersByTime(20);
    FakeTerminal.current.buffer.active.viewportY = 80;
    sockets[0]?.onmessage?.({ data: JSON.stringify({ type: 'output', data: 'tick' }) } as MessageEvent<string>);
    expect(FakeTerminal.current.buffer.active.viewportY).toBe(100);
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

it('initializes current appearance and configured scrollback, and updates font/theme without replacing the PTY', async () => {
  applyAppearance({ ...appearanceDefaults, terminalFont: 16, accent: 'teal' });
  document.documentElement.style.setProperty('--background', '#08090c'); document.documentElement.style.setProperty('--text', '#ffffff');
  const controller = new TerminalController({ cellId: 'preferences', sessionId: 's', surface: surface(), isActive: () => true, webSocketFactory: socketFactory, scrollback: '3500' });
  expect(terminalOptions).toMatchObject({ fontSize: 16, scrollback: 3500, theme: { background: '#08090c', foreground: '#ffffff', cursor: '#2dd4bf', selectionBackground: '#2dd4bf4d' } });
  sockets[0]?.onopen?.(); vi.advanceTimersByTime(20); const terminal = FakeTerminal.current; const fit = vi.spyOn(FakeFitAddon.prototype, 'fit');
  applyAppearance({ ...appearanceDefaults, terminalFont: 16, accent: 'amber' }); await Promise.resolve(); vi.advanceTimersByTime(20);
  expect(terminal.options.theme).toMatchObject({ cursor: '#f0ad39', selectionBackground: '#f0ad394d' }); expect(fit).not.toHaveBeenCalled();
  applyAppearance({ ...appearanceDefaults, terminalFont: 18, accent: 'amber' }); await Promise.resolve(); vi.advanceTimersByTime(20);
  expect(terminal.options.fontSize).toBe(18); expect(fit).toHaveBeenCalledTimes(1); expect(terminal.buffer.active.viewportY).toBe(60); expect(socketCreations).toBe(1); expect(terminalDisposals).toBe(0); expect(writes).toHaveLength(0);
  applyAppearance({ ...appearanceDefaults, terminalFont: 18, accent: 'amber' }); await Promise.resolve(); vi.advanceTimersByTime(20); expect(fit).toHaveBeenCalledTimes(1);
  controller.dispose();
});
it('preserves reading distance and tail intent when font preview and restoration refit the terminal', async () => {
  const controller = new TerminalController({ cellId: 'font-reader', sessionId: 's', surface: surface(), isActive: () => true, webSocketFactory: socketFactory });
  sockets[0]?.onopen?.(); vi.advanceTimersByTime(20); const terminal = FakeTerminal.current;
  vi.spyOn(FakeFitAddon.prototype, 'fit').mockImplementation(() => { terminal.buffer.active.baseY = 130; terminal.buffer.active.viewportY = 0; terminal.rows = 25; });
  applyAppearance({ ...appearanceDefaults, terminalFont: 18 }); await Promise.resolve(); vi.advanceTimersByTime(20);
  expect(terminal.buffer.active.viewportY).toBe(90); expect(terminal.options.fontSize).toBe(18);
  controller.scrollToTail(); applyAppearance(appearanceDefaults); await Promise.resolve(); vi.advanceTimersByTime(20);
  expect(terminal.buffer.active.viewportY).toBe(130); expect(terminal.options.fontSize).toBe(12);
  expect(sent.map((frame) => (JSON.parse(frame) as { type: string }).type).every((type) => type === 'resize')).toBe(true); controller.dispose();
});
it('updates hidden terminal preferences without emitting ownership frames, and disconnects the appearance observer on disposal', async () => {
  const controller = new TerminalController({ cellId: 'hidden-preferences', sessionId: 's', surface: surface(), isActive: () => false, webSocketFactory: socketFactory }); const terminal = FakeTerminal.current;
  const fit = vi.spyOn(FakeFitAddon.prototype, 'fit');
  applyAppearance({ ...appearanceDefaults, terminalFont: 20, accent: 'violet' }); controller.setScrollback(100); await Promise.resolve(); vi.advanceTimersByTime(20);
  expect(terminal.options).toMatchObject({ fontSize: 20, scrollback: 100, theme: { cursor: '#a78bfa' } }); expect(sent).toHaveLength(0); expect(fit).not.toHaveBeenCalled();
  controller.dispose(); applyAppearance(appearanceDefaults); controller.setScrollback(5000); await Promise.resolve(); vi.advanceTimersByTime(20);
  expect(terminal.options).toMatchObject({ fontSize: 20, scrollback: 100 }); expect(terminalDisposals).toBe(1);
});
it('sets normalized scrollback only when changed, without resetting output, focus or geometry', () => {
  const controller = new TerminalController({ cellId: 'scrollback-options', sessionId: 's', surface: surface(), isActive: () => true, webSocketFactory: socketFactory });
  expect(FakeTerminal.current.options.scrollback).toBe(2000); vi.advanceTimersByTime(20); sent.length = 0;
  let current = 2000; const set = vi.fn((value: number) => { current = value; });
  Object.defineProperty(FakeTerminal.current.options, 'scrollback', { get: () => current, set });
  controller.setScrollback(4000); controller.setScrollback('4000'); controller.setScrollback(100_001); controller.setScrollback(NaN);
  expect(set.mock.calls).toEqual([[4000], [2000]]); expect(writes).toHaveLength(0); expect(sent).toHaveLength(0); expect(socketCreations).toBe(1); controller.dispose();
});

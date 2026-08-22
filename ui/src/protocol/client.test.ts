import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createAppStore } from '../app/store';
import { TorqueProtocolClient, type WebSocketLike } from './client';
import { compactStateFixture, representativeDeltaFixture } from './fixtures';

class FakeSocket implements WebSocketLike {
  readyState: number = WebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: ((event: { code?: number; reason?: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  readonly sent: string[] = [];

  open(): void {
    this.readyState = WebSocket.OPEN;
    this.onopen?.();
  }

  receive(value: unknown): void {
    this.onmessage?.({ data: JSON.stringify(value) });
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    if (this.readyState === WebSocket.CLOSED) return;
    this.readyState = WebSocket.CLOSED;
    this.onclose?.({ code: 1000 });
  }
}

describe('TorqueProtocolClient', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it('connects, hydrates, and applies an in-sequence delta', () => {
    const store = createAppStore();
    const socket = new FakeSocket();
    const client = new TorqueProtocolClient({
      store,
      url: 'ws://example.test/ws?compact=1',
      webSocketFactory: () => socket,
      now: () => 1_000,
    });

    client.start();
    socket.open();
    socket.receive(compactStateFixture);
    socket.receive(representativeDeltaFixture);

    expect(store.getState().connection.status).toBe('connected');
    expect(store.getState().connection.expectedSeq).toBe(12);
    expect(store.getState().projection.seq).toBe(11);
    expect(store.getState().projection.data.agents).toHaveProperty('agent-2');
    client.stop();
  });

  it('requests one full resync when a sequence gap is observed', () => {
    const store = createAppStore();
    const socket = new FakeSocket();
    const client = new TorqueProtocolClient({
      store,
      url: 'ws://example.test/ws',
      webSocketFactory: () => socket,
      now: () => 2_000,
    });

    client.start();
    socket.open();
    socket.receive(compactStateFixture);
    socket.receive({ type: 'delta', seq: 99, ops: [] });
    socket.receive({ type: 'delta', seq: 100, ops: [] });

    expect(store.getState().connection.awaitingResync).toBe(true);
    expect(store.getState().connection.resyncCount).toBe(1);
    expect(socket.sent.map((value) => JSON.parse(value) as unknown)).toEqual([
      { cmd: 'resync', compact: 1 },
    ]);
    socket.receive({ ...compactStateFixture, seq: 100 });
    socket.receive({ type: 'delta', seq: 101, ops: [{ op: 'runtime', version: 'resynced' }] });
    expect(store.getState().connection.awaitingResync).toBe(false);
    expect(store.getState().connection.expectedSeq).toBe(102);
    expect(store.getState().projection.data.runtime).toMatchObject({ version: 'resynced' });
    client.stop();
  });

  it('resyncs instead of partially applying an unknown operation', () => {
    const store = createAppStore();
    const socket = new FakeSocket();
    const client = new TorqueProtocolClient({
      store,
      url: 'ws://example.test/ws',
      webSocketFactory: () => socket,
    });

    client.start();
    socket.open();
    socket.receive(compactStateFixture);
    socket.receive({ type: 'delta', seq: 11, ops: [{ op: 'future_operation', value: 1 }] });

    expect(store.getState().projection.seq).toBe(10);
    expect(store.getState().connection.awaitingResync).toBe(true);
    expect(socket.sent).toHaveLength(1);
    client.stop();
  });

  it('reconnects after close and records the reconnect', () => {
    const store = createAppStore();
    const sockets: FakeSocket[] = [];
    const client = new TorqueProtocolClient({
      store,
      url: 'ws://example.test/ws',
      reconnectDelayMs: 20,
      webSocketFactory: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket;
      },
    });

    client.start();
    sockets[0]?.open();
    sockets[0]?.close();
    vi.advanceTimersByTime(20);
    sockets[1]?.open();
    sockets[1]?.receive({ ...compactStateFixture, seq: 50, active_session_id: 'agent-1' });

    expect(sockets).toHaveLength(2);
    expect(store.getState().connection.status).toBe('connected');
    expect(store.getState().connection.reconnectCount).toBe(1);
    expect(store.getState().connection.expectedSeq).toBe(51);
    expect(store.getState().projection.data.active_session_id).toBe('agent-1');
    client.stop();
  });

  it('closes a stale open socket so the reconnect path can recover it', () => {
    let now = 1_000;
    const store = createAppStore();
    const socket = new FakeSocket();
    const client = new TorqueProtocolClient({
      store,
      url: 'ws://example.test/ws',
      webSocketFactory: () => socket,
      livenessCheckMs: 10,
      livenessStaleMs: 30,
      now: () => now,
    });

    client.start();
    socket.open();
    now = 1_031;
    vi.advanceTimersByTime(10);

    expect(socket.readyState).toBe(WebSocket.CLOSED);
    expect(store.getState().connection.status).toBe('disconnected');
    client.stop();
  });

  it('records malformed frames without losing the connection', () => {
    const store = createAppStore();
    const socket = new FakeSocket();
    const client = new TorqueProtocolClient({
      store,
      url: 'ws://example.test/ws',
      webSocketFactory: () => socket,
    });

    client.start();
    socket.open();
    socket.onmessage?.({ data: '{broken' });

    expect(store.getState().connection.status).toBe('connected');
    expect(store.getState().connection.diagnostics.at(-1)?.kind).toBe('parse');
    client.stop();
  });

  it('hydrates full task detail without replacing the compact task record', () => {
    const store = createAppStore();
    const socket = new FakeSocket();
    const client = new TorqueProtocolClient({
      store,
      url: 'ws://example.test/ws?compact=1',
      webSocketFactory: () => socket,
    });

    client.start();
    socket.open();
    socket.receive(compactStateFixture);
    socket.receive({
      type: 'task_detail',
      id: 'task-1',
      task: { id: 'task-1', description: 'Full task body', artifacts: [{ title: 'Report' }] },
    });

    expect(store.getState().projection.data.board_tasks).toMatchObject({
      'task-1': {
        id: 'task-1',
        task: 'Build the foundation',
        description: 'Full task body',
        artifacts: [{ title: 'Report' }],
      },
    });
    client.stop();
  });

  it('hydrates Phase 4 lazy resource responses into stable projection keys', () => {
    const store = createAppStore();
    const socket = new FakeSocket();
    const client = new TorqueProtocolClient({
      store,
      url: 'ws://example.test/ws?compact=1',
      webSocketFactory: () => socket,
    });

    client.start();
    socket.open();
    socket.receive(compactStateFixture);
    socket.receive({ type: 'initiative_list', group: 'Foundation', initiatives: [{ id: 'initiative-1', title: 'Phase 4' }] });
    socket.receive({ type: 'initiative_created', initiative: { id: 'initiative-2', title: 'Live mutation' } });
    socket.receive({ type: 'actions', group: 'Foundation', actions: [{ name: 'implement' }] });
    socket.receive({ type: 'scratchpad_note_list', group: 'Foundation', notes: [{ id: 'note-1', title: 'Explore' }] });
    socket.receive({ type: 'mission_control_summary', sections: { in_flight: [{ id: 'task-1' }] } });

    expect(store.getState().projection.data.initiatives).toMatchObject({
      'initiative-1': { title: 'Phase 4' },
      'initiative-2': { title: 'Live mutation' },
    });
    expect(store.getState().projection.data.actions).toEqual([{ name: 'implement' }]);
    expect(store.getState().projection.data.thinking_scratchpad_notes).toMatchObject({ 'note-1': { title: 'Explore' } });
    expect(store.getState().projection.data.mission_control_summary).toMatchObject({ sections: { in_flight: [{ id: 'task-1' }] } });
    client.stop();
  });
});

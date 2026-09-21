import { describe, expect, it } from 'vitest';

import {
  createAppStore,
  projectionActions,
  selectAgentsState,
} from '../app/store';
import {
  allKnownOperationsFixture,
  compactStateFixture,
  fullStateFixture,
  representativeDeltaFixture,
} from './fixtures';
import { KNOWN_DELTA_OPERATIONS, parseServerFrame } from './types';
import type { DeltaFrame, StateFrame } from './types';

describe('protocol projection', () => {
  it('hydrates a compact state frame without retaining transport fields', () => {
    const store = createAppStore();
    store.dispatch(projectionActions.snapshotReceived(compactStateFixture));

    const projection = store.getState().projection;
    expect(projection.hydrated).toBe(true);
    expect(projection.seq).toBe(10);
    expect(projection.data).not.toHaveProperty('type');
    expect(projection.data).not.toHaveProperty('seq');
    expect(projection.data.agents).toHaveProperty('agent-1');
  });

  it('hydrates a full snapshot through the same normalized projection boundary', () => {
    const store = createAppStore();
    store.dispatch(projectionActions.snapshotReceived(fullStateFixture));

    expect(store.getState().projection.seq).toBe(20);
    expect(store.getState().projection.data.agents).toHaveProperty('agent-1');
  });

  it('applies representative entity and runtime deltas', () => {
    const store = createAppStore();
    store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
    store.dispatch(projectionActions.deltaReceived(representativeDeltaFixture));

    const projection = store.getState().projection;
    expect(projection.seq).toBe(11);
    expect(projection.data.agents).toHaveProperty('agent-2');
    expect(projection.data.board_tasks).toHaveProperty('task-2');
    expect(projection.data.runtime).toMatchObject({ version: 'test-next' });
    expect(projection.data.active_group).toBe('Foundation');
    expect(projection.appliedOperationCount).toBe(4);
  });

  it('keeps domain selector references stable for unrelated deltas', () => {
    const store = createAppStore();
    store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
    const before = selectAgentsState(store.getState());

    store.dispatch(projectionActions.deltaReceived({
      type: 'delta',
      seq: 11,
      ops: [{ op: 'runtime', version: 'unrelated' }],
    }));

    expect(selectAgentsState(store.getState())).toBe(before);
  });

  it('recognizes and applies a fixture for every classic-client operation', () => {
    const store = createAppStore();
    store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
    store.dispatch(projectionActions.deltaReceived(allKnownOperationsFixture));

    const projection = store.getState().projection;
    expect(projection.unknownOperations).toEqual([]);
    expect(projection.appliedOperationCount).toBe(KNOWN_DELTA_OPERATIONS.length);
    expect(Object.keys(projection.lastDeltaByOperation).sort()).toEqual(
      [...KNOWN_DELTA_OPERATIONS].sort(),
    );
    expect(projection.data.agent_message_history).toHaveProperty('agent-2');
    expect(projection.data.behavior_overlay_versions).toHaveProperty('agent-2');
    expect(projection.data.mcp_calls).toHaveProperty('agent-2');
    expect(projection.data.operator_notices).toHaveProperty('notice-1');
    expect(projection.data.perceived_empty_episodes).toHaveProperty('agent-2');
    expect(projection.data.provider_usage).toHaveProperty('codex');
    expect(projection.data.worktree_merge_progress).toHaveProperty('merge-1');
  });

  it('hydrates and updates a representative large workspace within the Phase 5 budget', () => {
    const agents = Object.fromEntries(Array.from({ length: 500 }, (_, index) => [
      `agent-${index}`,
      { id: `agent-${index}`, name: `Agent ${index}`, kind: 'worker', group: 'Foundation', status: 'running' },
    ]));
    const boardTasks = Object.fromEntries(Array.from({ length: 2_000 }, (_, index) => [
      `task-${index}`,
      { id: `task-${index}`, task: `Task ${index}`, group: 'Foundation', lane: 'Backlog' },
    ]));
    const frame: StateFrame = { ...compactStateFixture, seq: 100, agents, board_tasks: boardTasks };
    const delta: DeltaFrame = {
      type: 'delta',
      seq: 101,
      ops: Array.from({ length: 1_000 }, (_, index) => ({
        op: 'agent_upsert' as const,
        id: `agent-${index % 500}`,
        name: `Agent ${index % 500}`,
        kind: 'worker',
        group: 'Foundation',
        status: index % 2 ? 'idle' : 'running',
      })),
    };
    const store = createAppStore();
    const started = performance.now();
    store.dispatch(projectionActions.snapshotReceived(frame));
    store.dispatch(projectionActions.deltaReceived(delta));
    const elapsed = performance.now() - started;

    expect(Object.keys(store.getState().projection.data.agents ?? {})).toHaveLength(500);
    expect(Object.keys(store.getState().projection.data.board_tasks ?? {})).toHaveLength(2_000);
    expect(store.getState().projection.appliedOperationCount).toBe(1_000);
    expect(elapsed).toBeLessThan(3_000);
  });
});

describe('frame parsing', () => {
  it('rejects malformed state and delta envelopes', () => {
    expect(parseServerFrame({ type: 'state', seq: -1 }).ok).toBe(false);
    expect(parseServerFrame({ type: 'delta', seq: 1, ops: {} }).ok).toBe(false);
    expect(parseServerFrame({ type: 'delta', seq: 1, ops: [{}] }).ok).toBe(false);
  });

  it('keeps auxiliary command frames available to feature clients', () => {
    const parsed = parseServerFrame({ type: 'metrics_tick', timestamp: 42 });
    expect(parsed).toEqual({
      ok: true,
      frame: { type: 'metrics_tick', timestamp: 42 },
    });
  });
});


it('merges older event pages and retains them when new live events arrive', () => {
  const store = createAppStore();
  store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, panel_events: Array.from({ length: 500 }, (_, i) => ({ id: i + 100, message: 'live' })) }));
  store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'events_page', events: [{ id: 5, message: 'older' }, { id: 100, message: 'updated' }] }));
  store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'event_append', id: 600, message: 'newest' }] }));
  const events = store.getState().projection.data.panel_events as { id: number; message: string }[];
  expect(events).toHaveLength(502);
  expect(events[0]?.id).toBe(5);
  expect(events.find((event) => event.id === 100)?.message).toBe('updated');
  expect(events.at(-1)?.id).toBe(600);
});

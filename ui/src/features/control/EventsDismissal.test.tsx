import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { ActivityPanel } from './OperatorPanels';

afterEach(() => vi.unstubAllGlobals());

it('dismisses an agent alert by its identity and source timestamp, retains history, and resurfaces newer attention', async () => {
  const store = createAppStore(); const send = vi.fn();
  const agent = { id: 'worker', name: 'Worker alert', group: 'A', cell_type: 'agent', needs_attention: true, activity_detail: 'Needs review', error_message: '', last_event_at: 50 };
  const snapshot = (stamp: number, dismissed: Record<string, number> = {}, detail = 'Needs review') => store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, agents: { worker: { ...agent, last_event_at: stamp, activity_detail: detail } }, board_tasks: {}, events_dismissed_attention: dismissed }));
  snapshot(50);
  render(<Provider store={store}><ActivityPanel group="A" events={[{ id: 321, kind: 'agent_blocked', message: 'Historical entry', cell_id: 'worker', timestamp: 50 }]} send={send} /></Provider>);
  const attention = screen.getByRole('region', { name: 'Attention requests' });
  fireEvent.click(within(attention).getByRole('button', { name: 'Dismiss attention for Worker alert' }));
  expect(send).toHaveBeenLastCalledWith({ cmd: 'events_dismiss', id: 'worker', timestamp: 50 });
  // A refused or unsent command must not hide the alert optimistically.
  expect(within(attention).getByText('Needs review')).toBeVisible();
  fireEvent.change(screen.getByLabelText('Search events'), { target: { value: 'Historical' } });
  fireEvent.click(screen.getByText('Historical entry'));
  await act(() => snapshot(50, { worker: 50 }));
  expect(within(attention).queryByText('Needs review')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Search events')).toHaveValue('Historical');
  expect(screen.getByText('Historical entry')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Copy event' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Dismiss attention' })).not.toBeInTheDocument();
  await act(() => snapshot(51, { worker: 50 }));
  expect(within(attention).getByText('Needs review')).toBeVisible();
  await act(() => snapshot(52, { worker: 50 }, ''));
  expect(within(attention).getByText('Needs attention')).toBeVisible();
  await act(() => snapshot(0, { worker: 50 }));
  expect(within(attention).queryByText('Needs review')).not.toBeInTheDocument();
});

it('honors saved ask dismissals without resolving the task and sends the ask creation timestamp', async () => {
  const store = createAppStore(); const send = vi.fn();
  const task = { id: 'ask', task: 'Review this change', group: 'A', labels: ['torque:human'], lane: 'Backlog', created_at: '2026-09-30T10:00:00Z', description: 'Keep the pending task' };
  const stamp = Date.parse(task.created_at) / 1000;
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'task_detail', id: task.id, task } }) }));
  const snapshot = (dismissed: Record<string, number>) => store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, agents: {}, board_tasks: { ask: task }, events_dismissed_attention: dismissed }));
  snapshot({ ask: stamp });
  render(<Provider store={store}><ActivityPanel group="A" events={[]} send={send} /></Provider>);
  expect(screen.queryByRole('region', { name: 'Response to Review this change' })).not.toBeInTheDocument();
  await act(() => snapshot({}));
  expect(await screen.findByRole('region', { name: 'Response to Review this change' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss attention for Review this change' }));
  expect(send).toHaveBeenCalledWith({ cmd: 'events_dismiss', id: 'ask', timestamp: stamp });
  expect(store.getState().projection.data.board_tasks).toEqual({ ask: task });
});

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { normalizeTasks } from './model';
import { StaleDoneArchive } from './StaleDoneArchive';
const now = Date.parse('2026-09-22T12:00:00Z');
const tasks = normalizeTasks({ one: { id: 'one', task: 'Older', lane: 'Done', group: 'Torque', updated_at: '2026-09-01T12:00:00Z' }, two: { id: 'two', task: 'Newer', lane: 'Done', group: 'Torque', updated_at: '2026-09-10T12:00:00Z' } });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });
it('sends one ordered batch, blocks repeated clicks, retains failure and acknowledges a retry', async () => {
  vi.spyOn(Date, 'now').mockReturnValue(now);
  const calls: unknown[] = []; const onArchived = vi.fn(); let finish: (payload: unknown) => void = () => { throw new Error('No request'); };
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => { calls.push(JSON.parse(options.body as string)); return new Promise((resolve) => { finish = (payload) => resolve({ ok: true, json: () => Promise.resolve(payload) }); }); }));
  const { rerender } = render(<StaleDoneArchive tasks={tasks} group="Torque" onArchived={onArchived} />);
  const archive = screen.getByRole('button', { name: 'Archive 2 completed tasks inactive for 7+ days' });
  fireEvent.click(archive); fireEvent.click(archive); expect(archive).toBeDisabled(); expect(calls).toEqual([{ cmd: 'board_archive_tasks', ids: ['one', 'two'] }]); expect(onArchived).not.toHaveBeenCalled();
  await act(async () => { finish({ ok: false, error: 'Atomic archive refused' }); await Promise.resolve(); });
  expect(await screen.findByRole('alert')).toHaveTextContent('Atomic archive refused'); expect(archive).toBeEnabled();
  // Newer projection state governs retry; a task moved to Ready is not included.
  const moved = tasks.map((task) => task.id === 'two' ? { ...task, lane: 'Ready' } : task);
  rerender(<StaleDoneArchive tasks={moved} group="Torque" onArchived={onArchived} />);
  fireEvent.click(screen.getByRole('button', { name: 'Archive 1 completed task inactive for 7+ days' }));
  expect(calls[1]).toEqual({ cmd: 'board_archive_tasks', ids: ['one'] });
  const frame = { type: 'toast', level: 'success', message: 'Archived 1 completed task' };
  await act(async () => { finish({ ok: true, data: frame }); await Promise.resolve(); });
  await waitFor(() => expect(onArchived).toHaveBeenCalledExactlyOnceWith(frame)); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  rerender(<StaleDoneArchive tasks={moved.map((task) => ({ ...task, lane: 'Archived' }))} group="Torque" onArchived={onArchived} />);
  expect(screen.queryByRole('region')).not.toBeInTheDocument();
});
it('reveals tasks crossing the cutoff while idle and removes its timer on unmount', () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  const soon = [{ ...tasks[0]!, updatedAt: new Date(now - 7 * 86400000 + 30_000).toISOString() }];
  const { unmount } = render(<StaleDoneArchive tasks={soon} group="Torque" onArchived={vi.fn()} />);
  expect(screen.queryByRole('button')).not.toBeInTheDocument(); act(() => { vi.advanceTimersByTime(60_000); });
  expect(screen.getByRole('button', { name: 'Archive 1 completed task inactive for 7+ days' })).toBeInTheDocument();
  unmount(); expect(vi.getTimerCount()).toBe(0);
});
it('does not report an unexpected acknowledgement as a successful archive', async () => {
  vi.spyOn(Date, 'now').mockReturnValue(now); const onArchived = vi.fn();
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'state' } }) })));
  render(<StaleDoneArchive tasks={tasks} group="Torque" onArchived={onArchived} />);
  fireEvent.click(screen.getByRole('button', { name: /Archive 2 completed/ })); expect(await screen.findByRole('alert')).toHaveTextContent('Could not confirm the archive'); expect(onArchived).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss archive error' })); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

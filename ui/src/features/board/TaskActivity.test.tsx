import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { TaskActivity } from './TaskActivity';
import { taskMessageDate } from './taskActivityModel';
afterEach(() => vi.unstubAllGlobals());
it('shows newest sequence first with actor and Unix-second timestamps', () => {
  render(<TaskActivity messages={[{ action: 'started', agent: 'Engineer', timestamp: 1700000000, message: 'First' }, { action: 'progress', agent: 'Worker', timestamp: '2023-11-14T23:00:00Z', message: 'Second' }]} />);
  const rows = screen.getAllByRole('article');
  expect(rows.map((row) => row.getAttribute('aria-label'))).toEqual(['Activity 2', 'Activity 1']);
  expect(within(rows[0]!).getByText('Worker')).toBeVisible();
  expect(rows[1]!.querySelector('time')).toHaveAttribute('datetime', '2023-11-14T22:13:20.000Z');
  expect(taskMessageDate('1700000000')?.toISOString()).toBe('2023-11-14T22:13:20.000Z');
  expect(taskMessageDate(1700000000000)?.toISOString()).toBe('2023-11-14T22:13:20.000Z');
  expect(taskMessageDate('invalid')).toBeNull(); expect(taskMessageDate(null)).toBeNull();
});
it('pages older entries and holds the current reading window when messages arrive', () => {
  const messages = Array.from({ length: 85 }, (_, index) => ({ message: `Message ${index + 1}` }));
  const { rerender } = render(<TaskActivity messages={messages} />);
  expect(screen.getAllByRole('article')).toHaveLength(40);
  fireEvent.click(screen.getByRole('button', { name: 'Load older activity · 45 remaining' }));
  const anchor = screen.getByRole('article', { name: 'Activity 85' });
  rerender(<TaskActivity messages={[...messages, { message: 'New arrival' }]} />);
  expect(screen.getByRole('article', { name: 'Activity 85' })).toBe(anchor);
  expect(screen.queryByText('New arrival')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Show 1 new messages' }));
  expect(screen.getAllByRole('article')[0]).toHaveTextContent('New arrival');
  fireEvent.click(screen.getByRole('button', { name: 'Load older activity · 6 remaining' }));
  expect(screen.getAllByRole('article')).toHaveLength(86);
});

it('retains hydrated rows through compact updates, retries failure and reads only while active', async () => {
  let finish!: (value: unknown) => void;
  const fetcher = vi.fn(() => new Promise((resolve) => { finish = resolve; })); vi.stubGlobal('fetch', fetcher);
  const messages = [{ message: 'Original message', timestamp: 1700000000 }];
  const { rerender } = render(<TaskActivity taskId="task" messages={messages} />);
  const compact = [{ count: 2, message: '2 updates' }];
  rerender(<TaskActivity taskId="task" messages={compact} active={false} />);
  expect(fetcher).not.toHaveBeenCalled(); expect(screen.getByText('Original message')).toBeVisible();
  rerender(<TaskActivity taskId="task" messages={compact} />);
  expect(screen.getByText('Original message')).toBeVisible();
  await act(async () => { finish({ ok: true, json: () => Promise.resolve({ ok: false, error: 'Read failed' }) }); await Promise.resolve(); });
  expect(await screen.findByRole('alert')).toHaveTextContent('Read failed');
  fireEvent.click(screen.getByRole('button', { name: 'Retry activity' }));
  await act(async () => { finish({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'task_detail', id: 'task', task: { messages: [...messages, { message: 'New message' }] } } }) }); await Promise.resolve(); });
  expect(await screen.findByRole('button', { name: 'Show 1 new messages' })).toBeVisible();
  expect(screen.queryByText('New message')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Show 1 new messages' }));
  expect(screen.getByText('New message')).toBeVisible();
});

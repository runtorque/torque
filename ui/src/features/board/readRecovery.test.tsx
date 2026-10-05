import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { readCommand } from '../../protocol/http';
import type { AuxiliaryFrame } from '../../protocol';
import { TaskActivity } from './TaskActivity';
import { TaskPromptPreview } from './TaskPromptPreview';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand); const compact = [{ count: 2 }];
function held() { let resolve!: (value: AuxiliaryFrame) => void; const promise = new Promise<AuxiliaryFrame>((done) => { resolve = done; }); return { promise, resolve }; }
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
const deadline = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(15_001); }); };
beforeEach(() => { read.mockReset(); vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });
it('bounds compact activity reads, retains the reading window and ignores late results after retry', async () => {
  const pending = held(); let signal: AbortSignal | undefined;
  read.mockImplementationOnce((_command, owner) => { signal = owner; return pending.promise; });
  read.mockResolvedValue({ type: 'task_detail', id: 'one', task: { id: 'one', messages: [{ message: 'Original' }, { message: 'Fresh' }] } });
  const { rerender } = render(<TaskActivity taskId="one" messages={[{ message: 'Original' }]} />);
  const original = screen.getByRole('article'); rerender(<TaskActivity taskId="one" messages={compact} />);
  await deadline(); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(signal?.aborted).toBe(true); expect(screen.getByRole('article')).toBe(original);
  fireEvent.click(screen.getByRole('button', { name: 'Retry activity' })); await flush();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument(); expect(screen.getByRole('article')).toBe(original);
  fireEvent.click(screen.getByRole('button', { name: 'Show 1 new messages' })); expect(screen.getByText('Fresh')).toBeVisible();
  pending.resolve({ type: 'task_detail', id: 'one', task: { messages: [{ message: 'Obsolete' }] } }); await flush();
  expect(screen.queryByText('Obsolete')).not.toBeInTheDocument(); expect(screen.getByText('Fresh')).toBeVisible();
});
it('distinguishes unloaded activity from empty history and rejects another task response', async () => {
  const pending = held(); read.mockReturnValueOnce(pending.promise); render(<TaskActivity taskId="one" messages={compact} />);
  expect(screen.getByRole('status')).toHaveTextContent('Loading task activity'); expect(screen.queryByText('No task activity yet.')).not.toBeInTheDocument();
  pending.resolve({ type: 'task_detail', id: 'other', task: { id: 'other', messages: [{ message: 'Unrelated' }] } }); await flush();
  expect(screen.getByRole('alert')).toHaveTextContent('Could not refresh activity'); expect(screen.queryByText('Unrelated')).not.toBeInTheDocument();
  read.mockResolvedValue({ type: 'task_detail', id: 'one', task: { id: 'one', messages: [] } });
  fireEvent.click(screen.getByRole('button', { name: 'Retry activity' })); await flush(); expect(screen.getByText('No task activity yet.')).toBeVisible();
});
it('cancels hidden activity and resets hydrated rows when the task changes', async () => {
  const pending = held(); let signal: AbortSignal | undefined;
  read.mockImplementation((_command, owner) => { signal = owner; return pending.promise; });
  const { rerender } = render(<TaskActivity taskId="one" messages={[{ message: 'First task' }]} />);
  rerender(<TaskActivity taskId="one" messages={compact} />); rerender(<TaskActivity taskId="one" messages={compact} active={false} />); expect(signal?.aborted).toBe(true);
  pending.resolve({ type: 'task_detail', id: 'one', task: { messages: [{ message: 'Hidden response' }] } }); await flush(); expect(screen.queryByText('Hidden response')).not.toBeInTheDocument();
  read.mockReturnValue(new Promise(() => {})); rerender(<TaskActivity taskId="two" messages={compact} />);
  expect(screen.queryByText('First task')).not.toBeInTheDocument(); expect(screen.getByRole('status')).toHaveTextContent('Loading task activity');
});
it('bounds preview requests and permits explicit retry without accepting a late response', async () => {
  const pending = held(); let signal: AbortSignal | undefined;
  read.mockImplementationOnce((_command, owner) => { signal = owner; return pending.promise; }); read.mockResolvedValue({ type: 'prompt_preview', prompt: 'Current preview' });
  render(<TaskPromptPreview inputsKey="draft" command={() => ({ cmd: 'preview_prompt', task: 'Draft' })} />);
  fireEvent.click(screen.getByRole('button', { name: 'Preview prompt' })); await deadline(); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(signal?.aborted).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Preview prompt' })); await flush(); expect(screen.getByText('Current preview')).toBeVisible();
  pending.resolve({ type: 'prompt_preview', prompt: 'Obsolete preview' }); await flush(); expect(screen.queryByText('Obsolete preview')).not.toBeInTheDocument();
});
it('cancels preview observation on unmount without leaking its deadline', async () => {
  let signal: AbortSignal | undefined; read.mockImplementation((_command, owner) => { signal = owner; return new Promise(() => {}); });
  const { unmount } = render(<TaskPromptPreview inputsKey="draft" command={() => ({ cmd: 'preview_prompt' })} />);
  fireEvent.click(screen.getByRole('button', { name: 'Preview prompt' })); unmount(); await flush(); expect(signal?.aborted).toBe(true); expect(vi.getTimerCount()).toBe(0);
});

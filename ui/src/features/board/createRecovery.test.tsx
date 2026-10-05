import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { TorqueCommand } from '../../protocol';
import { TaskCreateDialog } from './TaskCreateDialog';
function held<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
const response = (data: unknown) => ({ ok: true, status: 200, json: () => Promise.resolve(data) });
const created = { ok: true, data: { type: 'board_task_added', task_id: 'QA:1', title: 'Reviewed draft' } };
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
const deadline = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(30_001); }); };
function setup() {
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
  const onClose = vi.fn(); const afterCreate = vi.fn(() => Promise.resolve());
  const view = render(<Provider store={store}><TaskCreateDialog group="Foundation" lanes={['Backlog']} actions={[]} roles={[]} onClose={onClose} afterCreate={afterCreate} /></Provider>);
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Reviewed draft' } });
  return { ...view, onClose, afterCreate };
}
beforeEach(() => vi.useFakeTimers());
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
it('retains submitted creation after timeout and retries its exact payload and key', async () => {
  const pending = held<ReturnType<typeof response>>(); const calls: TorqueCommand[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => { calls.push(JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand); return calls.length === 1 ? pending.promise : Promise.resolve(response(created)); }));
  const { onClose, afterCreate } = setup(); fireEvent.click(screen.getByRole('button', { name: 'Create task' })); await deadline();
  expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(screen.getByLabelText('Title')).toHaveValue('Reviewed draft'); expect(screen.getByLabelText('Title')).toBeDisabled();
  expect(calls[0]?.idempotency_key).toEqual(expect.any(String)); expect(calls[0]?.idempotency_key).not.toBe(calls[0]?.id);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' })); await flush(); expect(onClose).not.toHaveBeenCalled(); expect(screen.getByRole('alert')).toHaveTextContent('Retry creation');
  fireEvent.click(screen.getByRole('button', { name: 'Retry creation' })); await flush(); expect(calls[1]).toEqual(calls[0]); expect(afterCreate).toHaveBeenCalledExactlyOnceWith('QA:1'); expect(onClose).toHaveBeenCalledOnce();
  pending.resolve(response(created)); await flush(); expect(afterCreate).toHaveBeenCalledOnce();
});
it('rejects a wrong creation acknowledgement without linking its ID', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(response({ ok: true, data: { type: 'task_detail', task_id: 'wrong' } }))));
  const { onClose, afterCreate } = setup(); fireEvent.click(screen.getByRole('button', { name: 'Create task' })); await flush();
  expect(screen.getByRole('alert')).toHaveTextContent('invalid creation acknowledgement'); expect(afterCreate).not.toHaveBeenCalled(); expect(onClose).not.toHaveBeenCalled(); expect(screen.getByRole('button', { name: 'Retry creation' })).toBeEnabled();
});
it('cancels unmounted creation before a late result can start linking', async () => {
  const pending = held<ReturnType<typeof response>>(); let signal: AbortSignal | undefined;
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => { signal = options.signal as AbortSignal; return pending.promise; }));
  const { unmount, onClose, afterCreate } = setup(); fireEvent.click(screen.getByRole('button', { name: 'Create task' })); unmount(); expect(signal?.aborted).toBe(true);
  pending.resolve(response(created)); await flush(); expect(afterCreate).not.toHaveBeenCalled(); expect(onClose).not.toHaveBeenCalled();
});
it('retains accepted uploads after a later upload body timeout and ignores late metadata', async () => {
  const pending = held<unknown>(); let count = 0;
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => { count++; const id = (options.body as FormData).get('task_id') as string; return Promise.resolve({ ok: true, json: () => count === 1 ? Promise.resolve({ ok: true, data: [{ filename: 'first.png', path: `/attachments/${id}/first.png`, mime_type: 'image/png' }] }) : pending.promise }); }));
  setup(); fireEvent.click(screen.getByText('Attachments and artifacts · 0')); fireEvent.change(screen.getByLabelText('Upload evidence files'), { target: { files: [new File(['a'], 'first.png', { type: 'image/png' }), new File(['b'], 'late.png', { type: 'image/png' })] } }); await flush(); await deadline();
  expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(screen.getByRole('button', { name: 'Remove attachment first.png' })).toBeVisible(); expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled();
  pending.resolve({ ok: true, data: [{ filename: 'late.png', path: '/attachments/other/late.png', mime_type: 'image/png' }] }); await flush(); expect(screen.queryByRole('button', { name: 'Remove attachment late.png' })).not.toBeInTheDocument();
});
it('bounds draft cleanup and retries closing without losing the reviewed draft', async () => {
  const pending = held<ReturnType<typeof response>>(); let cleanups = 0;
  vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => {
    if (url === '/api/upload/cleanup') { cleanups++; return cleanups === 1 ? pending.promise : Promise.resolve(response({ ok: true })); }
    const id = (options.body as FormData).get('task_id') as string; return Promise.resolve(response({ ok: true, data: [{ filename: 'a.png', path: `/attachments/${id}/a.png`, mime_type: 'image/png' }] }));
  }));
  const { onClose } = setup(); fireEvent.click(screen.getByText('Attachments and artifacts · 0')); fireEvent.change(screen.getByLabelText('Upload evidence files'), { target: { files: [new File(['a'], 'a.png', { type: 'image/png' })] } }); await flush();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' })); await deadline(); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(screen.getByLabelText('Title')).toHaveValue('Reviewed draft'); expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Create task' })).toBeDisabled(); expect(screen.getByLabelText('Title')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' })); await flush(); expect(onClose).toHaveBeenCalledOnce(); pending.resolve(response({ ok: true })); await flush(); expect(onClose).toHaveBeenCalledOnce();
});

it('keeps the creation key for malformed envelopes but releases a refused draft for correction', async () => {
  const calls: TorqueCommand[] = []; let mode = 'malformed';
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => { calls.push(JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand); return Promise.resolve(response(mode === 'malformed' ? { ok: true } : mode === 'refused' ? { ok: false, creation_refused: true, error: 'Invalid lane' } : { ...created, data: { ...created.data, title: 'Corrected draft' } })); }));
  const { onClose } = setup(); fireEvent.click(screen.getByRole('button', { name: 'Create task' })); await flush(); expect(screen.getByRole('button', { name: 'Retry creation' })).toBeEnabled();
  mode = 'refused'; fireEvent.click(screen.getByRole('button', { name: 'Retry creation' })); await flush(); expect(calls[1]).toEqual(calls[0]); expect(screen.getByLabelText('Title')).toBeEnabled();
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Corrected draft' } }); mode = 'valid'; fireEvent.click(screen.getByRole('button', { name: 'Create task' })); await flush(); expect(calls[2]?.idempotency_key).not.toBe(calls[0]?.idempotency_key); expect(onClose).toHaveBeenCalledOnce();
});
it('retains draft evidence when removal times out and validates the retry acknowledgement', async () => {
  const pending = held<ReturnType<typeof response>>(); let attempts = 0;
  vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => {
    if (url === '/api/cmd') { attempts++; return attempts === 1 ? pending.promise : Promise.resolve(response({ ok: true, data: attempts === 2 ? { type: 'ok' } : { type: 'state', seq: 1, board_tasks: {} } })); }
    const id = (options.body as FormData).get('task_id') as string; return Promise.resolve(response({ ok: true, data: [{ filename: 'a.png', path: `/attachments/${id}/a.png`, mime_type: 'image/png' }] }));
  }));
  setup(); fireEvent.click(screen.getByText('Attachments and artifacts · 0')); fireEvent.change(screen.getByLabelText('Upload evidence files'), { target: { files: [new File(['a'], 'a.png', { type: 'image/png' })] } }); await flush();
  fireEvent.click(screen.getByRole('button', { name: 'Remove attachment a.png' })); await deadline(); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(screen.getByRole('button', { name: 'Remove attachment a.png' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Create task' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Retry removal' })); await flush(); expect(screen.getByRole('alert')).toHaveTextContent('invalid cleanup acknowledgement');
  fireEvent.click(screen.getByRole('button', { name: 'Retry removal' })); await flush(); expect(screen.queryByRole('button', { name: 'Remove attachment a.png' })).not.toBeInTheDocument(); pending.resolve(response({ ok: true, data: { type: 'state', seq: 1, board_tasks: {} } })); await flush();
});

it('keeps the original intent after an unclassified server error that may follow mutation', async () => {
  const calls: TorqueCommand[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => { calls.push(JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand); return Promise.resolve(response({ ok: false, error: 'File finalization failed' })); }));
  const { onClose } = setup(); fireEvent.click(screen.getByRole('button', { name: 'Create task' })); await flush(); expect(screen.getByLabelText('Title')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Retry creation' })); await flush(); expect(calls[1]).toEqual(calls[0]); expect(onClose).not.toHaveBeenCalled();
});

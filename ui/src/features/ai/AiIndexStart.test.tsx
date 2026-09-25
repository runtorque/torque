import { act, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createAppStore, projectionActions, selectOperationsState } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { readCommand } from '../../protocol/http';
import type { AuxiliaryFrame } from '../../protocol';
import { AiIndexStart } from './AiIndexStart';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand); beforeEach(() => { read.mockReset(); }); afterEach(() => { vi.useRealTimers(); });
function setup(store = createAppStore(), disabled = false) { return { store, ...render(<Provider store={store}><AiIndexStart disabled={disabled} /></Provider>) }; }
function pending() { let resolve!: (frame: AuxiliaryFrame) => void; read.mockImplementationOnce(() => new Promise((finish) => { resolve = finish; })); return async (frame: AuxiliaryFrame) => { await act(async () => { resolve(frame); await Promise.resolve(); }); }; }
const start = () => fireEvent.click(screen.getByRole('button', { name: 'Build index' }));
const accepted = { type: 'ai_index_job', job: { id: 'job-1', status: 'queued', mode: 'incremental' } };
it('starts only explicitly with one in-flight request and adopts an acknowledged job', async () => {
  const finish = pending(); const { store } = setup(); expect(read).not.toHaveBeenCalled(); start(); expect(read.mock.calls[0]![0]).toEqual({ cmd: 'ai_index_start', mode: 'incremental', confirm: true }); expect(screen.getByRole('button')).toBeDisabled(); fireEvent.click(screen.getByRole('button')); expect(read).toHaveBeenCalledTimes(1);
  await finish(accepted); expect(screen.getByRole('status')).toHaveTextContent('Start request accepted. Job: job-1.'); expect(selectOperationsState(store.getState()).aiSettings.index).toMatchObject({ status: 'building', current_job: accepted.job }); expect(screen.getByRole('button')).toBeDisabled();
});
it('retains pending ownership through close/reopen and never replays after a snapshot', async () => {
  const finish = pending(); const { store, unmount } = setup(); start(); unmount(); expect(read.mock.calls[0]![1].aborted).toBe(false); setup(store); expect(screen.getByRole('button')).toBeDisabled();
  act(() => { store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); }); await finish(accepted); expect(read).toHaveBeenCalledTimes(1); expect(screen.getByRole('status')).toHaveTextContent('job-1'); expect(selectOperationsState(store.getState()).aiSettings).not.toHaveProperty('index');
});
it('never lets a late queued acknowledgement replace newer completed-job diagnostics', async () => {
  const finish = pending(); const { store } = setup(); start();
  act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 1, ops: [{ op: 'ai_index_status_update', index: { status: 'ready', current_job: { id: 'job-1', status: 'completed' }, counts: { indexed: 9 } } }] })); });
  await finish({ ...accepted, settings: { index: { status: 'building', current_job: accepted.job, counts: { indexed: 0 } } } }); expect(selectOperationsState(store.getState()).aiSettings.index).toMatchObject({ status: 'ready', current_job: { status: 'completed' }, counts: { indexed: 9 } }); expect(screen.getByRole('button', { name: 'Rebuild index' })).toBeEnabled();
});
it.each([{ type: 'ok' }, { type: 'ai_index_job', job: null }, { type: 'ai_index_job', job: { id: '', status: 'queued' } }])('rejects malformed start acknowledgement %j', async (frame) => {
  const finish = pending(); setup(); start(); await finish(frame); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(screen.getByRole('button')).toBeEnabled();
});
it('shows refusal and supports only explicit retry', async () => {
  const first = pending(); const second = pending(); setup(); start(); await first({ type: 'error', message: 'Dependency refused token=not-displayable' }); expect(screen.getByRole('alert')).toHaveTextContent('Dependency refused token: [redacted]'); expect(read).toHaveBeenCalledTimes(1); start(); await second(accepted); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
it('times out without automatic replay and ignores an obsolete acknowledgement after retry', async () => {
  vi.useFakeTimers(); const first = pending(); const second = pending(); setup(); start(); await act(async () => { vi.advanceTimersByTime(30_000); await Promise.resolve(); }); expect(read.mock.calls[0]![1].aborted).toBe(true); expect(screen.getByRole('alert')).toHaveTextContent('Check the live job status before retrying'); start(); await first(accepted); expect(screen.getByRole('button')).toHaveTextContent('Starting index'); await second({ ...accepted, job: { id: 'job-2', status: 'completed' } }); expect(screen.getByRole('status')).toHaveTextContent('job-2');
});
it('respects saving and live gates without issuing requests', () => {
  const store = createAppStore(); const view = setup(store, true); expect(screen.getByRole('button')).toBeDisabled(); view.rerender(<Provider store={store}><AiIndexStart /></Provider>);
  act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'ai_settings', settings: { embeddings: { dependency: { status: 'missing' } } } })); }); expect(screen.getByRole('button')).toBeDisabled(); fireEvent.click(screen.getByRole('button')); expect(read).not.toHaveBeenCalled();
});

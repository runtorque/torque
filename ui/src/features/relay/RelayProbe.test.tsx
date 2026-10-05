import { act, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { RelayProbe } from './RelayProbe';
import { relayProbeActions } from './relayProbeState';
import { readCommand } from '../../protocol/http';
import type { AuxiliaryFrame } from '../../protocol';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
beforeEach(() => read.mockReset()); afterEach(() => vi.useRealTimers());
function setup(store = createAppStore(), disabled = false) { return { store, ...render(<Provider store={store}><RelayProbe disabled={disabled} /></Provider>) }; }
function pending() { let finish!: (value: AuxiliaryFrame) => void; read.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; })); return async (value: AuxiliaryFrame) => { await act(async () => { finish(value); await Promise.resolve(); }); }; }
const testConnection = () => fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
it('runs only on demand, prevents duplicates and shows complete acknowledged results', async () => {
  const finish = pending(); setup(); expect(read).not.toHaveBeenCalled(); testConnection(); expect(read.mock.calls[0]![0]).toEqual({ cmd: 'test_relay_connection' }); expect(screen.getByRole('button', { name: 'Testing connection…' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Testing connection…' })); expect(read).toHaveBeenCalledTimes(1); expect(screen.getByRole('status')).toHaveTextContent('Testing Relay');
  await finish({ type: 'relay_test_result', status: 'ca_missing', message: 'Install the CA bundle.', detail: 'Certificate verification failed\nMissing root CA.' });
  expect(screen.getByRole('status')).toHaveAttribute('data-tone', 'danger'); expect(screen.getByRole('status')).toHaveTextContent('CA certificate unavailable'); expect(screen.getByText('Install the CA bundle.')).toBeVisible(); expect(screen.getByText(/Missing root CA/)).toBeVisible();
});
it('keeps a pending probe and its eventual result across Settings unmount and compact reconnect snapshots', async () => {
  const finish = pending(); const { store, unmount } = setup(); testConnection(); unmount(); expect(read.mock.calls[0]![1]?.aborted).toBe(false);
  const again = setup(store); expect(screen.getByRole('button', { name: 'Testing connection…' })).toBeDisabled(); act(() => { store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); }); expect(read).toHaveBeenCalledTimes(1);
  await finish({ type: 'relay_test_result', status: 'reachable_unauthed', message: 'Reachable', detail: 'No credentials available' }); const detail = screen.getByText('No credentials available'); detail.tabIndex = 0; detail.focus();
  act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'relay_connection', status: 'connected' }] })); }); expect(screen.getByText('No credentials available')).toBe(detail); expect(detail).toHaveFocus();
  again.unmount(); setup(store); expect(screen.getByRole('status')).toHaveTextContent('No credentials available'); expect(read).toHaveBeenCalledTimes(1);
});
it.each([{ type: 'ok' }, { type: 'relay_test_result', status: '' }, { type: 'relay_test_result', status: 3 }])('rejects malformed results %j without rendering a cached success', async (frame) => {
  const finish = pending(); setup(); testConnection(); await finish(frame); expect(screen.getByRole('alert')).toHaveTextContent('invalid connection-test result'); expect(screen.queryByRole('status')).not.toBeInTheDocument();
});
it('reports a refusal, retains unknown future statuses and requires explicit retry', async () => {
  const failed = pending(); const ready = pending(); setup(); testConnection(); await failed({ type: 'error', message: 'Probe unavailable' }); expect(screen.getByRole('alert')).toHaveTextContent('Probe unavailable'); expect(read).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Retry connection test' })); await ready({ type: 'relay_test_result', status: 'future_probe', message: 'A newer status', detail: 'Future detail' }); expect(screen.getByRole('status')).toHaveAttribute('data-tone', 'warning'); expect(screen.getByRole('status')).toHaveTextContent('future_probe');
});
it('bounds the pending operation and ignores late or superseded replies', async () => {
  vi.useFakeTimers(); const first = pending(); const second = pending(); const { store } = setup(); testConnection(); const firstId = store.getState().relayProbe.id;
  await act(async () => { vi.advanceTimersByTime(15_000); await Promise.resolve(); }); expect(read.mock.calls[0]![1]?.aborted).toBe(true); expect(screen.getByRole('alert')).toHaveTextContent('timed out');
  fireEvent.click(screen.getByRole('button', { name: 'Retry connection test' })); await first({ type: 'relay_test_result', status: 'ok', message: 'Obsolete success' }); expect(screen.queryByText('Obsolete success')).not.toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Testing connection…' })).toBeDisabled();
  act(() => { store.dispatch(relayProbeActions.succeeded({ id: firstId, result: { status: 'ok', message: 'Old direct frame', detail: '' } })); }); expect(store.getState().relayProbe.phase).toBe('pending');
  await second({ type: 'relay_test_result', status: 'ok', message: 'Current success' }); expect(screen.getByRole('status')).toHaveTextContent('Current success'); expect(read).toHaveBeenCalledTimes(2);
});
it('disables initiation while Settings is saving without issuing a request', () => { setup(undefined, true); expect(screen.getByRole('button', { name: 'Test connection' })).toBeDisabled(); expect(read).not.toHaveBeenCalled(); });

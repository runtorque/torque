import { act, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { browserHost } from '../../host';
import { LogViewer } from './LogViewer';
import { SupervisorDetails } from './OperationalDetails';
const settle = async () => { await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); }); };
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
function harness() {
  const store = createAppStore();
  store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
  store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  store.dispatch(connectionActions.snapshotAccepted(compactStateFixture));
  const reconnect = () => act(() => { store.dispatch(connectionActions.disconnected({ at: 2 })); store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); });
  return { store, reconnect };
}
it('refreshes paused Supervisor on reconnect, retaining sort, disclosure, reading position and pause', async () => {
  vi.useFakeTimers(); const h = harness(); let pid = 7;
  const fetcher = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'supervisor_sessions', sessions: [{ session_id: 'stable', owner: { name: 'Worker' }, display_command: 'worker-shell', pid, alive: true }] } }) }));
  vi.stubGlobal('fetch', fetcher);
  const view = render(<Provider store={h.store}><SupervisorDetails supervisor={{}} send={vi.fn()} onTerminate={vi.fn()} /></Provider>); await settle();
  fireEvent.click(screen.getByLabelText('Auto-refresh sessions')); await settle();
  fireEvent.change(screen.getByLabelText('Sort sessions'), { target: { value: 'pid' } });
  const row = screen.getByRole('button', { name: /worker-shell/ }); fireEvent.click(row);
  const viewport = row.closest('article')!.parentElement!; viewport.scrollTop = 120; row.focus();
  const before = fetcher.mock.calls.length; pid = 9; h.reconnect(); await settle();
  expect(fetcher).toHaveBeenCalledTimes(before + 1); expect(row).toHaveTextContent('9');
  expect(row).toHaveAttribute('aria-expanded', 'true'); expect(row).toHaveFocus(); expect(viewport.scrollTop).toBe(120);
  expect(screen.getByLabelText('Sort sessions')).toHaveValue('pid'); expect(screen.getByLabelText('Auto-refresh sessions')).not.toBeChecked();
  await act(() => vi.advanceTimersByTimeAsync(10_000)); expect(fetcher).toHaveBeenCalledTimes(before + 1);
  view.unmount(); h.reconnect(); await settle(); expect(fetcher).toHaveBeenCalledTimes(before + 1);
});
it('refreshes paused Logs on reconnect without resetting filters, cursor, focus, scroll or Follow', async () => {
  vi.useFakeTimers(); const h = harness(); let cursor = 0;
  const fetcher = vi.fn((url: string) => Promise.resolve({ ok: true, json: () => Promise.resolve({ target: 'daemon', inode: 'same', size: 1000, cursor: ++cursor, lines: [{ ts: cursor, level: 'INFO', message: `accepted ${url} ${cursor}` }] }) }));
  vi.stubGlobal('fetch', fetcher);
  const view = render(<Provider store={h.store}><LogViewer host={browserHost} /></Provider>); await settle();
  fireEvent.click(screen.getByLabelText('Follow')); await settle();
  fireEvent.change(screen.getByLabelText('Level'), { target: { value: 'INFO' } });
  const search = screen.getByLabelText('Search logs'); fireEvent.change(search, { target: { value: 'accepted' } }); search.focus();
  const viewport = screen.getByRole('log'); viewport.scrollTop = 100;
  const before = fetcher.mock.calls.length; const lastCursor = cursor; h.reconnect(); await settle();
  expect(fetcher).toHaveBeenCalledTimes(before + 1); expect(fetcher.mock.calls.at(-1)?.[0]).toContain(`since=${lastCursor}`);
  expect(screen.getByLabelText('Follow')).not.toBeChecked(); expect(search).toHaveValue('accepted'); expect(search).toHaveFocus(); expect(viewport.scrollTop).toBe(100);
  expect(screen.getByLabelText('Level')).toHaveValue('INFO'); expect(viewport).toHaveTextContent(`since=${lastCursor}`);
  await act(() => vi.advanceTimersByTimeAsync(10_000)); expect(fetcher).toHaveBeenCalledTimes(before + 1);
  view.unmount(); h.reconnect(); await settle(); expect(fetcher).toHaveBeenCalledTimes(before + 1);
});

it.each(['logs', 'supervisor'] as const)('discards a late %s read replaced by reconnect', async (surface) => {
  const h = harness();
  const requests: { signal: AbortSignal; resolve: (response: unknown) => void }[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => new Promise((resolve) => requests.push({ signal: options.signal as AbortSignal, resolve }))));
  render(<Provider store={h.store}>{surface === 'logs' ? <LogViewer host={browserHost} /> : <SupervisorDetails supervisor={{}} send={vi.fn()} onTerminate={vi.fn()} />}</Provider>); await settle();
  expect(requests).toHaveLength(1); h.reconnect(); await settle(); expect(requests).toHaveLength(2); expect(requests[0]!.signal.aborted).toBe(true);
  const response = (label: string) => ({ ok: true, json: () => Promise.resolve(surface === 'logs' ? { target: 'daemon', cursor: 1, lines: [{ message: label }] } : { ok: true, data: { type: 'supervisor_sessions', sessions: [{ session_id: 'stable', owner: { name: label }, alive: true }] } }) });
  requests[1]!.resolve(response('Current response')); await settle(); expect(screen.getByText('Current response', { exact: true })).toBeVisible();
  requests[0]!.resolve(response('Obsolete response')); await settle(); expect(screen.queryByText(/Obsolete response/)).not.toBeInTheDocument(); expect(screen.getByText('Current response', { exact: true })).toBeVisible();
});

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { UnknownRecord } from '../../protocol';
import { HealthDetails } from './OperationalDetails';

const settle = async () => { await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); }); };
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
function harness() {
  const store = createAppStore();
  store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
  const connect = (at = 1) => { store.dispatch(connectionActions.connected({ at, reconnect: at !== 1 })); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); };
  connect();
  const calls: { command: UnknownRecord; signal: AbortSignal }[] = [];
  const frame = (command: UnknownRecord): UnknownRecord => ({ type: command.cmd === 'get_metrics_history' ? 'metrics_history' : 'system_health_metrics', group: command.cmd === 'get_metrics_history' && command.group === '' ? 'Foundation' : command.group, window: command.window, scope: command.group ? 'group' : 'all_groups', bucket_seconds: 3600, buckets: [{ label: '01:00' }, { label: '02:00' }], perf: { rss_mb: [100, 110] }, series: { dispatches: [1, 2] } });
  let response: (command: UnknownRecord, signal: AbortSignal) => Promise<UnknownRecord> = (command) => Promise.resolve(frame(command));
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as UnknownRecord;
    const signal = options.signal as AbortSignal; calls.push({ command, signal });
    const data = await response(command, signal);
    return { ok: true, json: () => Promise.resolve({ ok: true, data }) };
  }));
  const view = render(<Provider store={store}><HealthDetails group="Foundation" runtime={{ supervisor: { connected: true } }} /></Provider>);
  const tick = (value: UnknownRecord) => act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'metrics_tick', ...value })); });
  return { store, view, tick, calls, connect, frame, respond: (next: typeof response) => { response = next; } };
}
const liveTick = {
  enabled: true, generated_at: 1_790_000_000, interval_ms: 2000,
  perf: { event_loop_lag_ms: { p50: 1, p95: 2, max: 3 }, ws: { deltas_per_s: 4, bytes_per_s: 500, subscribers: 6 }, db: { writes_per_s: 7, write_latency_p95_ms: 8 }, proc: { rss_mb: 900, cpu_pct: 10 }, live: { agents: 11, ptys: 12, prompt_queue_depth: 13, supervisor_connected: true, supervisor_latency_ms: 14, stuck_sessions: 15 }, frontend: { render_per_s: 16, render_ms_p95: 17 } },
  meter_overhead: { agg_tick_ms: 0.12, collect_overhead_pct: 0.34 },
};

describe('live Health display', () => {
  it('renders every Classic live metric from daemon ticks and preserves expanded history and focus', async () => {
    const h = harness(); await settle(); expect(screen.getByText('Waiting for live metrics…')).toBeVisible();
    const graph = screen.getByRole('img', { name: /Process memory history/ });
    const details = graph.closest('article')!.querySelector('details')!; details.open = true;
    const windowChoice = screen.getByLabelText('Health history window'); windowChoice.focus();
    h.tick(liveTick);
    const expected: Record<string, string[]> = {
      'Event-loop lag': ['2.0 ms', '1.0 ms', '3.0 ms'], 'WebSocket throughput': ['4.0 /s', '500 B/s', '6'],
      'DB writes': ['8.0 ms', '7.0 /s'], 'Process memory': ['900 MB', '10.0 %'], 'Process CPU': ['10.0 %', '900 MB'],
      'Live counts': ['11 agents', '12', '13'], 'Supervisor link': ['Connected', '14.0 ms', '15'], 'Frontend renders': ['16.0 /s', '17.0 ms'],
    };
    for (const [name, values] of Object.entries(expected)) {
      const card = screen.getByRole('article', { name: `${name} live` });
      for (const value of values) expect(within(card).getByText(value, { exact: true })).toBeVisible();
    }
    for (const value of ['2000 ms', '0.12 ms', '0.34 %']) expect(screen.getByText(value, { exact: true })).toBeVisible();
    expect(screen.getByRole('img', { name: /Process memory history/ })).toBe(graph); expect(details.open).toBe(true); expect(windowChoice).toHaveFocus();
    expect(screen.queryByRole('img', { name: /Frontend.*history/ })).not.toBeInTheDocument();
    expect(h.calls).toHaveLength(2);
  });
  it('distinguishes missing, zero, disabled and disconnected measurements', async () => {
    const h = harness(); await settle();
    h.tick({ ...liveTick, perf: { frontend: { render_per_s: 0, render_ms_p95: 0 }, ws: { subscribers: null, bytes_per_s: NaN } } });
    const frontend = screen.getByRole('article', { name: 'Frontend renders live' }); expect(frontend).toHaveTextContent('0.0 /s'); expect(frontend).toHaveTextContent('0.0 ms');
    expect(screen.getByRole('article', { name: 'WebSocket throughput live' })).not.toHaveTextContent('0.0');
    h.tick({ ...liveTick, perf: { frontend: null } }); expect(screen.getByText('Frontend not reporting')).toBeVisible();
    h.tick({ ...liveTick, enabled: false }); expect(screen.getByText('Metrics collection is off.')).toBeVisible(); expect(frontend).not.toHaveTextContent('16.0');
    h.tick(liveTick); act(() => { h.store.dispatch(connectionActions.disconnected({ at: 2 })); });
    expect(screen.getByText('Offline · showing last received metrics.')).toBeVisible(); expect(frontend).toHaveTextContent('16.0 /s');
    act(() => { h.store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'global_settings', settings: { metrics_enabled: false } })); });
    expect(screen.getByText('Metrics collection is off.')).toBeVisible(); expect(frontend).not.toHaveTextContent('16.0');
  });
});

describe('visible Health history lifecycle', () => {
  it('refreshes every minute without resetting chart disclosures and stops when disconnected or unmounted', async () => {
    vi.useFakeTimers(); const h = harness(); await settle(); expect(h.calls).toHaveLength(2);
    const graph = screen.getByRole('img', { name: /Process memory history/ }); const disclosure = graph.closest('article')!.querySelector('details')!; disclosure.open = true;
    await act(() => vi.advanceTimersByTimeAsync(59_999)); expect(h.calls).toHaveLength(2);
    await act(() => vi.advanceTimersByTimeAsync(1)); expect(h.calls).toHaveLength(4); expect(disclosure.open).toBe(true); expect(screen.getByRole('img', { name: /Process memory history/ })).toBe(graph);
    act(() => { h.store.dispatch(connectionActions.disconnected({ at: 2 })); });
    await act(() => vi.advanceTimersByTimeAsync(60_000)); expect(h.calls).toHaveLength(4); expect(graph).toBeVisible();
    act(() => { h.connect(3); }); await settle(); expect(h.calls).toHaveLength(6);
    h.view.unmount(); await act(() => vi.advanceTimersByTimeAsync(120_000)); expect(h.calls).toHaveLength(6);
  });
  it('keeps accepted data on refusal, retries manually and rejects mismatched response scope', async () => {
    const h = harness(); await settle(); const graph = screen.getByRole('img', { name: /Process memory history/ });
    h.respond(() => Promise.reject(new Error('Injected read failure'))); fireEvent.click(screen.getByRole('button', { name: 'Refresh health' })); await settle();
    expect(screen.getByRole('alert')).toHaveTextContent('Injected read failure'); expect(graph).toBeVisible();
    h.respond((command) => Promise.resolve({ ...h.frame(command), group: 'Wrong scope' })); fireEvent.click(screen.getByRole('button', { name: 'Refresh health' })); await settle();
    expect(screen.getByRole('alert')).toHaveTextContent('did not match'); expect(graph).toBeVisible();
    h.respond((command) => Promise.resolve(h.frame(command))); fireEvent.click(screen.getByRole('button', { name: 'Refresh health' })); await settle(); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
  it('aborts replaced scope reads and ignores late replies without exposing old-scope errors', async () => {
    const h = harness(); await settle(); const pending: { command: UnknownRecord; signal: AbortSignal; resolve: (frame: UnknownRecord) => void }[] = [];
    h.respond((command, signal) => new Promise((resolve) => { pending.push({ command, signal, resolve }); }));
    fireEvent.change(screen.getByLabelText('Health history window'), { target: { value: '7d' } }); await settle(); expect(pending).toHaveLength(2);
    h.respond((command) => Promise.resolve(h.frame(command)));
    fireEvent.change(screen.getByLabelText('Health scope'), { target: { value: 'all' } }); await settle();
    expect(pending.every((request) => request.signal.aborted)).toBe(true);
    await act(async () => { for (const request of pending) request.resolve({ ...h.frame(request.command), perf: { rss_mb: [9999] } }); await Promise.resolve(); });
    expect(screen.getByRole('img', { name: /Process memory history/ })).not.toHaveAccessibleName(/9999/); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(h.calls.at(-1)?.command).toMatchObject({ group: '', window: '7d' });
  });
  it('keeps scope and window choices when Health is reopened without changing the main navigation preference', async () => {
    const h = harness(); await settle();
    fireEvent.change(screen.getByLabelText('Health scope'), { target: { value: 'all' } });
    fireEvent.change(screen.getByLabelText('Health history window'), { target: { value: '30d' } }); await settle();
    h.view.unmount(); const revision = h.store.getState().workspaceUi.navigationRevision;
    render(<Provider store={h.store}><HealthDetails group="Other group" runtime={{}} /></Provider>); await settle();
    expect(screen.getByLabelText('Health scope')).toHaveValue('all'); expect(screen.getByLabelText('Health history window')).toHaveValue('30d');
    expect(h.calls.at(-1)?.command).toMatchObject({ group: '', window: '30d' }); expect(revision).toBe(0);
  });
  it('bounds stalled reads, avoids overlapping polls and recovers on the next scheduled attempt', async () => {
    vi.useFakeTimers(); const h = harness(); await settle();
    h.respond((_command, signal) => new Promise((_resolve, reject) => { signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }); }));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh health' })); await settle(); expect(h.calls).toHaveLength(4);
    await act(() => vi.advanceTimersByTimeAsync(19_999)); expect(h.calls).toHaveLength(4);
    await act(() => vi.advanceTimersByTimeAsync(1)); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(h.calls.at(-1)?.signal.aborted).toBe(true);
    h.respond((command) => Promise.resolve(h.frame(command)));
    await act(() => vi.advanceTimersByTimeAsync(60_000)); expect(h.calls).toHaveLength(6); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

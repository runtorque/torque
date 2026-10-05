import { act, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore } from './store';
import { RenderTelemetry } from './RenderTelemetry';
import { RenderWindow, startRenderReports } from './frontendRenderMetrics';

const cleanups: (() => void)[] = [];
afterEach(() => { cleanups.splice(0).forEach((dispose) => dispose()); vi.useRealTimers(); vi.unstubAllGlobals(); });
function mockReports() {
  const calls: { body: Record<string, unknown>; signal: AbortSignal; resolve: () => void; reject: () => void }[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => new Promise((resolve, reject) => {
    calls.push({ body: JSON.parse(typeof options.body === 'string' ? options.body : '{}') as Record<string, unknown>, signal: options.signal as AbortSignal, resolve: () => resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'ok' } }) }), reject: () => reject(new Error('Telemetry unavailable')) });
    options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
  })));
  return calls;
}

describe('actual render work window', () => {
  it('matches Classic rate and percentile, keeps the inclusive window and reports zero after idle', () => {
    const samples = new RenderWindow(); expect(samples.stats(100_000)).toBeNull();
    samples.record(10, 100_000); samples.record(20, 101_000); samples.record(30, 102_000);
    expect(samples.stats(102_000)).toEqual({ render_per_s: 1.5, render_ms_p95: 30 });
    expect(samples.stats(105_000)).toEqual({ render_per_s: 0.6, render_ms_p95: 30 });
    expect(samples.stats(107_001)).toEqual({ render_per_s: 0, render_ms_p95: 0 });
  });
  it('bounds retention to the newest 240 samples and uses nearest-rank p95', () => {
    const samples = new RenderWindow();
    for (let i = 1; i <= 300; i++) samples.record(i, i);
    expect(samples.stats(300)).toEqual({ render_per_s: 240, render_ms_p95: 288 });
    const percentile = new RenderWindow();
    for (let i = 1; i <= 20; i++) percentile.record(i, i);
    expect(percentile.stats(20)?.render_ms_p95).toBe(19);
  });
  it('uses monotonic zero timestamps and finite nonnegative durations', () => {
    const samples = new RenderWindow(); samples.record(5, 0); samples.record(-4, 1); samples.record(NaN, 2);
    expect(samples.stats(2_000)).toEqual({ render_per_s: 1.5, render_ms_p95: 5 });
  });
});

describe('best-effort render reporting', () => {
  it('reports on a two-second cadence without overlaps, retries failures, and clears idle samples', async () => {
    vi.useFakeTimers(); const calls = mockReports(); const samples = new RenderWindow();
    cleanups.push(startRenderReports(samples));
    await vi.advanceTimersByTimeAsync(2_000); expect(calls).toHaveLength(0);
    samples.record(12, performance.now());
    await vi.advanceTimersByTimeAsync(2_000); expect(calls[0]?.body).toEqual({ cmd: 'report_frontend_render', render_per_s: 0.5, render_ms_p95: 12 });
    await vi.advanceTimersByTimeAsync(2_000); expect(calls).toHaveLength(1);
    calls[0]!.reject(); await vi.advanceTimersByTimeAsync(2_000);
    expect(calls[1]?.body).toEqual({ cmd: 'report_frontend_render', render_per_s: 0, render_ms_p95: 0 });
    calls[1]!.resolve(); await vi.advanceTimersByTimeAsync(2_000); expect(calls).toHaveLength(3);
  });
  it('aborts stalled reports and allows the following cadence to retry; disposal stops traffic', async () => {
    vi.useFakeTimers(); const calls = mockReports(); const samples = new RenderWindow(); samples.record(10, performance.now());
    const dispose = startRenderReports(samples); cleanups.push(dispose);
    await vi.advanceTimersByTimeAsync(12_000); expect(calls[0]?.signal.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(2_000); expect(calls.length).toBeGreaterThan(1);
    dispose(); expect(calls.at(-1)?.signal.aborted).toBe(true); const count = calls.length;
    await vi.advanceTimersByTimeAsync(20_000); expect(calls).toHaveLength(count);
  });
  it('profiles child-only commits and keeps acknowledgements out of render state; reconnect resumes reporting', async () => {
    vi.useFakeTimers(); const calls = mockReports(); const store = createAppStore();
    const recorded = vi.spyOn(RenderWindow.prototype, 'record'); let parentRenders = 0;
    function Child() { const [value, setValue] = useState(0); return <button onClick={() => setValue(value + 1)}>Child {value}</button>; }
    function Parent() { parentRenders++; return <Child />; }
    const view = render(<Provider store={store}><RenderTelemetry><Parent /></RenderTelemetry></Provider>); cleanups.push(view.unmount);
    const initialCommits = recorded.mock.calls.length; expect(initialCommits).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: 'Child 0' })); expect(parentRenders).toBe(1); expect(recorded.mock.calls.length).toBeGreaterThan(initialCommits);
    await act(() => vi.advanceTimersByTimeAsync(2_000)); expect(calls).toHaveLength(0);
    act(() => { store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); });
    await act(() => vi.advanceTimersByTimeAsync(2_000)); expect(calls).toHaveLength(1);
    const beforeAck = recorded.mock.calls.length; calls[0]!.resolve();
    await act(() => vi.advanceTimersByTimeAsync(2_000)); expect(recorded).toHaveBeenCalledTimes(beforeAck); expect(calls).toHaveLength(2);
    act(() => { store.dispatch(connectionActions.disconnected({ at: 2 })); }); expect(calls[1]!.signal.aborted).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(4_000)); expect(calls).toHaveLength(2);
    act(() => { store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); });
    await act(() => vi.advanceTimersByTimeAsync(2_000)); expect(calls).toHaveLength(3);
    view.unmount(); expect(calls[2]!.signal.aborted).toBe(true);
  });
});

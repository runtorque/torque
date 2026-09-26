import { act, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { createAppStore } from '../../app/store';
import { browserHost } from '../../host';
import { LogViewer } from './LogViewer';
import type { LogPage } from './logModel';

const settle = async () => { await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); }); };
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
type Request = { url: string; signal: AbortSignal; resolve: (response: Response) => void };
function harness() {
  vi.useFakeTimers();
  const requests: Request[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => new Promise<Response>((resolve) => requests.push({ url, signal: options.signal as AbortSignal, resolve }))));
  const view = render(<Provider store={createAppStore()}><LogViewer host={browserHost} /></Provider>);
  return { requests, view };
}
function page(cursor: number, message: string): LogPage { return { target: 'daemon', inode: 'same-file', size: 10000, cursor, lines: message ? [{ ts: cursor, level: 'INFO', message }] : [] }; }
function response(value: LogPage): Response { return { ok: true, json: () => Promise.resolve(value) } as Response; }
async function stall(request: Request, stage: 'request' | 'body') {
  let release = (value: LogPage) => request.resolve(response(value));
  if (stage === 'body') {
    const body = new Promise<LogPage>((resolve) => { release = resolve; });
    request.resolve({ ok: true, json: () => body } as Response);
    await settle();
  }
  return release;
}

it.each(['request', 'body'] as const)('initial log %s timeout exposes recovery instead of perpetual loading', async (stage) => {
  const { requests, view } = harness();
  const release = await stall(requests[0]!, stage);
  await act(() => vi.advanceTimersByTimeAsync(15_000));
  expect(screen.getByRole('alert')).toHaveTextContent(/log.*timed out/i);
  expect(screen.getByRole('log')).toHaveTextContent('Logs unavailable.');
  expect(requests[0]!.signal.aborted).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Refresh logs' })); await settle();
  expect(requests).toHaveLength(2);
  requests[1]!.resolve(response(page(7, 'Accepted initial tail'))); await settle();
  expect(screen.getByRole('log')).toHaveTextContent('Accepted initial tail'); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  release(page(999, 'Obsolete initial tail')); await settle();
  expect(screen.getByRole('log')).not.toHaveTextContent('Obsolete initial tail');
  view.unmount(); await act(() => vi.advanceTimersByTimeAsync(20_000)); expect(requests).toHaveLength(2);
});

it.each([
  ['request', true], ['body', true], ['request', false], ['body', false],
] as const)('log %s deadline retains reading state and recovers with Follow=%s', async (stage, follow) => {
  const { requests, view } = harness();
  requests[0]!.resolve(response(page(7, 'retained first line'))); await settle();
  if (!follow) { fireEvent.click(screen.getByLabelText('Follow')); await settle(); requests[1]!.resolve(response(page(7, ''))); await settle(); }
  fireEvent.change(screen.getByLabelText('Level'), { target: { value: 'INFO' } });
  const search = screen.getByLabelText<HTMLInputElement>('Search logs');
  fireEvent.change(search, { target: { value: 'retained' } }); search.focus(); search.setSelectionRange(2, 5);
  const log = screen.getByRole('log'); log.scrollTop = 80;
  if (follow) await act(() => vi.advanceTimersByTimeAsync(2_000));
  else { fireEvent.click(screen.getByRole('button', { name: 'Refresh logs' })); await settle(); }
  const expired = requests.at(-1)!; const release = await stall(expired, stage); const before = requests.length;
  expect(expired.url).toContain('since=7');
  await act(() => vi.advanceTimersByTimeAsync(15_000));
  expect(screen.getByRole('alert')).toHaveTextContent(/log.*timed out/i); expect(expired.signal.aborted).toBe(true);
  expect(log).toHaveTextContent('retained first line'); expect(log.scrollTop).toBe(80);
  expect(search).toHaveFocus(); expect(search).toHaveValue('retained'); expect([search.selectionStart, search.selectionEnd]).toEqual([2, 5]);
  expect(screen.getByLabelText('Level')).toHaveValue('INFO'); expect(screen.getByLabelText<HTMLInputElement>('Follow').checked).toBe(follow);
  if (follow) await act(() => vi.advanceTimersByTimeAsync(2_000));
  else { await act(() => vi.advanceTimersByTimeAsync(20_000)); expect(requests).toHaveLength(before); fireEvent.click(screen.getByRole('button', { name: 'Refresh logs' })); await settle(); }
  expect(requests).toHaveLength(before + 1); expect(requests.at(-1)!.url).toContain('since=7');
  requests.at(-1)!.resolve(response(page(8, 'retained recovered line'))); await settle();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument(); expect(log).toHaveTextContent('retained recovered line');
  release(page(999, 'retained obsolete line')); await settle(); expect(log).not.toHaveTextContent('obsolete');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh logs' })); await settle(); expect(requests.at(-1)!.url).toContain('since=8');
  view.unmount(); expect(requests.at(-1)!.signal.aborted).toBe(true); const count = requests.length;
  await act(() => vi.advanceTimersByTimeAsync(20_000)); expect(requests).toHaveLength(count);
});

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { toAgentViewModel } from '../agents/model';
import { TerminalWorkspace } from './TerminalSurface';
import { acquireTerminalController } from './terminalController';

const lease = vi.hoisted(() => ({ controller: { setScrollback: vi.fn(), focus: vi.fn(), paste: vi.fn() }, release: vi.fn() }));
vi.mock('./terminalController', () => ({ acquireTerminalController: vi.fn(() => lease) }));
beforeEach(() => { vi.clearAllMocks(); vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
function setup() {
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
  const content = (active = true, session = 'session') => {
    const cell = toAgentViewModel('drop', { name: 'Drop', kind: 'terminal', session_id: session });
    return <Provider store={store}><TerminalWorkspace agent={cell} terminal={cell} active={active} messages={[]} sendCommand={() => true} onUnavailable={() => {}} showConversation={false} /></Provider>;
  };
  const view = render(content());
  return { ...view, content, drop: (...names: string[]) => fireEvent.drop(vi.mocked(acquireTerminalController).mock.calls.at(-1)![1].surface, { dataTransfer: { files: names.map((name) => new File(['png'], name, { type: 'image/png' })) } }) };
}
const response = (path: unknown, ok = true) => ({ ok, json: () => Promise.resolve({ ok, data: [{ path, filename: 'image.png' }] }) });
const flush = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(0); }); };
it('never pastes an empty batch and exposes refused uploads for explicit retry', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response('/refused', false)));
  const view = setup(); view.drop('refused.png'); await flush();
  expect(lease.controller.paste).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent('refused.png');
  expect(lease.controller.focus).toHaveBeenCalledOnce();
});
it('retains successful images in input order, rejects invalid paths and shell-quotes paths', async () => {
  let first!: (value: unknown) => void;
  const fetcher = vi.fn().mockImplementationOnce(() => new Promise((resolve) => { first = resolve; }))
    .mockResolvedValueOnce(response('/second image.png')).mockResolvedValueOnce(response(''));
  vi.stubGlobal('fetch', fetcher); const view = setup(); view.drop('first.png', 'second.png', 'invalid.png'); await flush();
  expect(fetcher).toHaveBeenCalledTimes(3);
  first(response("/first's.png")); await flush();
  expect(lease.controller.paste).toHaveBeenCalledExactlyOnceWith("'/first'\"'\"'s.png' '/second image.png' ");
  expect(screen.getByRole('alert')).toHaveTextContent('invalid.png');
});
it.each(['request', 'body'])('bounds stalled %s observation and ignores its late receipt during a fresh drop', async (phase) => {
  let finish!: (value: unknown) => void;
  const pending = new Promise((resolve) => { finish = resolve; });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(phase === 'request' ? pending : { ok: true, json: () => pending }).mockResolvedValueOnce(response('/retry.png')));
  const view = setup(); view.drop('expired.png'); await flush();
  await act(async () => { await vi.advanceTimersByTimeAsync(30_001); });
  expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(lease.controller.paste).not.toHaveBeenCalled();
  view.drop('retry.png'); await flush();
  finish(phase === 'request' ? response('/expired.png') : { ok: true, data: [{ path: '/expired.png' }] }); await flush();
  expect(lease.controller.paste).toHaveBeenCalledExactlyOnceWith("'/retry.png' ");
});
it.each(['revisit', 'session', 'unmount'])('never pastes or focuses after the original lease ends: %s', async (change) => {
  let finish!: (value: unknown) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise((resolve) => { finish = resolve; })));
  const view = setup(); view.drop('late.png'); await flush();
  if (change === 'revisit') { view.rerender(view.content(false)); view.rerender(view.content(true)); }
  else if (change === 'session') view.rerender(view.content(true, 'replacement'));
  else view.unmount();
  finish(response('/late.png')); await flush();
  expect(lease.controller.paste).not.toHaveBeenCalled(); expect(lease.controller.focus).not.toHaveBeenCalled();
});
it.each(['transport', 'json'])('contains %s failure and still pastes a later successful file', async (phase) => {
  const failed = phase === 'transport' ? Promise.reject(new Error('Network failed')) : Promise.resolve({ ok: true, json: () => Promise.reject(new Error('Invalid JSON')) });
  vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(failed).mockResolvedValueOnce(response('/accepted.png')));
  const view = setup(); view.drop('failed.png', 'accepted.png'); await flush();
  expect(lease.controller.paste).toHaveBeenCalledExactlyOnceWith("'/accepted.png' ");
  expect(screen.getByRole('alert')).toHaveTextContent('Successful image paths were pasted');
  expect(screen.getByRole('alert')).toHaveTextContent('Drop the failed images again');
});
it.each([null, 123, {}, '\u0000bad', '/line\nbreak', '/escape\u001bpath'])('rejects malformed terminal paths: %j', async (path) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(path)));
  const view = setup(); view.drop('invalid.png'); await flush();
  expect(lease.controller.paste).not.toHaveBeenCalled(); expect(screen.getByRole('alert')).toHaveTextContent('Invalid image upload acknowledgement');
});
it('retains a successful image when another stalls, without replaying it on retry', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementationOnce(() => new Promise(() => {})).mockResolvedValueOnce(response('/success.png')).mockResolvedValueOnce(response('/retry.png')));
  const view = setup(); view.drop('stalled.png', 'success.png'); await flush();
  await act(async () => { await vi.advanceTimersByTimeAsync(30_001); });
  expect(lease.controller.paste).toHaveBeenCalledExactlyOnceWith("'/success.png' "); expect(screen.getByRole('alert')).toHaveTextContent('stalled.png');
  view.drop('stalled.png'); await flush();
  expect(lease.controller.paste.mock.calls).toEqual([["'/success.png' "], ["'/retry.png' "]]);
});

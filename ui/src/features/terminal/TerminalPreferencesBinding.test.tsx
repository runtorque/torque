import { act, render } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { toAgentViewModel } from '../agents/model';
import { TerminalWorkspace } from './TerminalSurface';
import { acquireTerminalController } from './terminalController';
const lease = vi.hoisted(() => ({ controller: { setScrollback: vi.fn(), focus: vi.fn() }, release: vi.fn() }));
vi.mock('./terminalController', () => ({ acquireTerminalController: vi.fn(() => lease) }));
beforeEach(() => { vi.clearAllMocks(); });
it('binds initial, live, acknowledged and resynced saved scrollback without remounting the terminal', () => {
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, global_settings: { xterm_scrollback: 7000 } }));
  const agent = toAgentViewModel('prefs', { name: 'Prefs', kind: 'worker', session_id: 's' });
  const content = (active: boolean) => <Provider store={store}><TerminalWorkspace agent={agent} terminal={agent} active={active} messages={[]} sendCommand={() => true} onUnavailable={() => {}} showConversation={false} /></Provider>;
  const view = render(content(true));
  expect(acquireTerminalController).toHaveBeenCalledTimes(1); expect(vi.mocked(acquireTerminalController).mock.calls[0]?.[1].scrollback).toBe(7000);
  act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: store.getState().projection.seq + 1, ops: [{ op: 'global_settings_update', xterm_scrollback: 100 }] })); });
  expect(lease.controller.setScrollback).toHaveBeenLastCalledWith(100);
  act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'global_settings', settings: { xterm_scrollback: 6000 } })); });
  expect(lease.controller.setScrollback).toHaveBeenLastCalledWith(6000);
  act(() => { store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, seq: store.getState().projection.seq + 1, global_settings: { xterm_scrollback: 9000 } })); });
  expect(lease.controller.setScrollback).toHaveBeenLastCalledWith(9000); expect(acquireTerminalController).toHaveBeenCalledTimes(1); expect(lease.release).not.toHaveBeenCalled();
  view.rerender(content(false)); expect(lease.release).toHaveBeenCalledTimes(1);
  act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'global_settings', settings: { xterm_scrollback: 4000 } })); });
  view.rerender(content(true)); expect(vi.mocked(acquireTerminalController).mock.calls.at(-1)?.[1].scrollback).toBe(4000); view.unmount();
});


afterEach(() => { vi.useRealTimers(); });
it('consumes explicit terminal focus once and cancels hidden or unmounted activation', () => {
  vi.useFakeTimers();
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
  const agent = toAgentViewModel('focus', { name: 'Focus', kind: 'worker', session_id: 'session' });
  const content = (active: boolean, focusRequest: number) => <Provider store={store}><TerminalWorkspace agent={agent} terminal={agent} active={active} focusRequest={focusRequest} messages={[]} sendCommand={() => true} onUnavailable={() => {}} showConversation={false} /></Provider>;
  const view = render(content(true, 0)); act(() => { vi.advanceTimersByTime(20); }); expect(lease.controller.focus).not.toHaveBeenCalled();
  view.rerender(content(true, 1)); act(() => { vi.advanceTimersByTime(20); }); expect(lease.controller.focus).toHaveBeenCalledTimes(1);
  view.rerender(content(false, 1)); view.rerender(content(true, 1)); act(() => { vi.advanceTimersByTime(20); }); expect(lease.controller.focus).toHaveBeenCalledTimes(1);
  view.rerender(content(true, 2)); view.rerender(content(false, 0)); act(() => { vi.advanceTimersByTime(20); }); expect(lease.controller.focus).toHaveBeenCalledTimes(1);
  view.rerender(content(true, 3)); act(() => { vi.advanceTimersByTime(20); }); expect(lease.controller.focus).toHaveBeenCalledTimes(2);
  view.rerender(content(true, 4)); view.unmount(); act(() => { vi.advanceTimersByTime(20); }); expect(lease.controller.focus).toHaveBeenCalledTimes(2);
});

it('disconnects the previous PTY surface immediately when its cell or session changes', () => {
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
  const content = (id: string, session: string) => {
    const agent = toAgentViewModel(id, { name: id, kind: 'worker', session_id: session });
    return <Provider store={store}><TerminalWorkspace agent={agent} terminal={agent} messages={[]} sendCommand={() => true} onUnavailable={() => {}} showConversation={false} /></Provider>;
  };
  const view = render(content('first', 'one'));
  const first = vi.mocked(acquireTerminalController).mock.calls.at(-1)![1].surface;
  view.rerender(content('second', 'two'));
  const second = vi.mocked(acquireTerminalController).mock.calls.at(-1)![1].surface;
  expect(first.isConnected).toBe(false); expect(second).not.toBe(first); expect(second.isConnected).toBe(true);
  view.rerender(content('second', 'restarted'));
  expect(second.isConnected).toBe(false); expect(vi.mocked(acquireTerminalController).mock.calls.at(-1)![1].surface.isConnected).toBe(true);
});

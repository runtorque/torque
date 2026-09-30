import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions, workspaceUiActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { readCommand } from '../../protocol/http';
import type { AuxiliaryFrame } from '../../protocol';
import { BoardPanel } from './BoardPanel';
import { StaleDoneArchive } from './StaleDoneArchive';
import { normalizeTasks } from './model';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const task = { id: 'one', task: 'First task', group: 'Foundation', lane: 'Backlog' };
const detail = (id = 'one'): AuxiliaryFrame => ({ type: 'task_detail', id, task: { ...task, id, description: 'Hydrated description', action_vars: {}, attachments: [] } });
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
function held() { let resolve!: (value: AuxiliaryFrame) => void; const promise = new Promise<AuxiliaryFrame>((done) => { resolve = done; }); return { promise, resolve }; }
function mountBoard() {
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { one: task, two: { ...task, id: 'two', task: 'Second task' } } }));
  store.dispatch(workspaceUiActions.setDetailTask('one'));
  const view = render(<Provider store={store}><BoardPanel group="Foundation" sendCommand={() => true} onCommandUnavailable={vi.fn()} /></Provider>);
  return { ...view, store };
}
beforeEach(() => { read.mockReset(); vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-26T12:00:00Z')); });
afterEach(() => { cleanup(); vi.useRealTimers(); });
it('bounds initial hydration, validates the target on retry and ignores the late initial response', async () => {
  const pending = held(); let signal: AbortSignal | undefined;
  read.mockImplementationOnce((_command, owner) => { signal = owner; return pending.promise; }); read.mockResolvedValueOnce(detail('other')); read.mockImplementation((command) => Promise.resolve(command.cmd === 'list_actions' ? { type: 'actions', actions: [] } : command.cmd === 'list_roles' ? { type: 'roles', roles: [] } : detail()));
  mountBoard(); expect(screen.getByText('Loading task')).toBeVisible(); await advance(15_001);
  expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(signal?.aborted).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Retry task details' })); await flush();
  expect(screen.getByRole('alert')).toHaveTextContent('did not match'); expect(screen.queryByRole('textbox', { name: 'Description' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry task details' })); await flush();
  const draft = screen.getByRole('textbox', { name: 'Description' }); fireEvent.change(draft, { target: { value: 'Keep my draft' } }); draft.focus();
  pending.resolve({ ...detail(), task: { ...task, description: 'Obsolete response' } }); await flush();
  expect(screen.getByRole('textbox', { name: 'Description' })).toBe(draft); expect(draft).toHaveValue('Keep my draft'); expect(draft).toHaveFocus();
});
it('cancels replaced and closed targets so old detail cannot hydrate another editor', async () => {
  const first = held(); let firstSignal: AbortSignal | undefined;
  read.mockImplementationOnce((_command, owner) => { firstSignal = owner; return first.promise; }); read.mockReturnValue(new Promise(() => {}));
  const { store, unmount } = mountBoard(); act(() => { store.dispatch(workspaceUiActions.setDetailTask('two')); }); await flush();
  expect(firstSignal?.aborted).toBe(true); expect(read).toHaveBeenLastCalledWith({ cmd: 'task_detail', id: 'two' }, expect.any(AbortSignal));
  first.resolve(detail()); await flush(); expect(screen.queryByRole('textbox', { name: 'Description' })).not.toBeInTheDocument();
  const lastSignal = read.mock.calls.at(-1)?.[1]; unmount(); await flush(); expect(lastSignal?.aborted).toBe(true);
});
it('retains a mounted draft when reconnect hydration times out and explicitly recovers', async () => {
  read.mockImplementation((command) => Promise.resolve(command.cmd === 'list_actions' ? { type: 'actions', actions: [] } : command.cmd === 'list_roles' ? { type: 'roles', roles: [] } : detail())); const { store } = mountBoard(); await flush();
  const draft = screen.getByRole('textbox', { name: 'Description' }); fireEvent.change(draft, { target: { value: 'Retained reconnect draft' } }); draft.focus();
  let holdDetail = true;
  read.mockImplementation((command) => { if (command.cmd === 'task_detail' && holdDetail) { holdDetail = false; return new Promise(() => {}); } return Promise.resolve(command.cmd === 'list_actions' ? { type: 'actions', actions: [] } : command.cmd === 'list_roles' ? { type: 'roles', roles: [] } : detail()); });
  act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { one: task } })); });
  await advance(15_001); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(screen.getByRole('textbox', { name: 'Description' })).toBe(draft); expect(draft).toHaveValue('Retained reconnect draft'); expect(draft).toHaveFocus();
  fireEvent.click(screen.getByRole('button', { name: 'Retry task details' })); await flush(); expect(screen.queryByRole('alert')).not.toBeInTheDocument(); expect(screen.getByRole('textbox', { name: 'Description' })).toBe(draft); expect(draft).toHaveValue('Retained reconnect draft');
});
const tasks = normalizeTasks({ one: { ...task, lane: 'Done', updated_at: '2026-09-01T12:00:00Z' }, two: { ...task, id: 'two', group: 'Other', lane: 'Done', updated_at: '2026-09-01T12:00:00Z' } });
it('bounds archive observation, ignores a late acknowledgement and retries only current eligible tasks', async () => {
  const pending = held(); let signal: AbortSignal | undefined; const done = vi.fn();
  read.mockImplementationOnce((_command, owner) => { signal = owner; return pending.promise; }); read.mockResolvedValue({ type: 'toast', level: 'success', message: 'Archived 1 completed task' });
  render(<StaleDoneArchive tasks={tasks} group="Foundation" onArchived={done} />); fireEvent.click(screen.getByRole('button', { name: /Archive 1 completed/ })); await advance(30_001);
  expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(signal?.aborted).toBe(true); expect(done).not.toHaveBeenCalled();
  pending.resolve({ type: 'toast', level: 'success', message: 'Late success' }); await flush(); expect(done).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /Archive 1 completed/ })); await flush(); expect(done).toHaveBeenCalledTimes(1); expect(read.mock.calls.map(([command]) => command)).toEqual([{ cmd: 'board_archive_tasks', ids: ['one'] }, { cmd: 'board_archive_tasks', ids: ['one'] }]);
});
it('cancels an archive when its group is replaced and never publishes the obsolete acknowledgement', async () => {
  const pending = held(); let signal: AbortSignal | undefined; const done = vi.fn();
  read.mockImplementationOnce((_command, owner) => { signal = owner; return pending.promise; }); read.mockResolvedValue({ type: 'toast', level: 'success', message: 'Current success' });
  const { rerender } = render(<StaleDoneArchive tasks={tasks} group="Foundation" onArchived={done} />); fireEvent.click(screen.getByRole('button', { name: /Archive 1 completed/ }));
  rerender(<StaleDoneArchive tasks={tasks} group="Other" onArchived={done} />); await flush(); expect(signal?.aborted).toBe(true);
  pending.resolve({ type: 'toast', level: 'success', message: 'Obsolete success' }); await flush(); expect(done).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /Archive 1 completed/ })); await flush(); expect(read.mock.calls.at(-1)?.[0]).toEqual({ cmd: 'board_archive_tasks', ids: ['two'] }); expect(done).toHaveBeenCalledExactlyOnceWith({ type: 'toast', level: 'success', message: 'Current success' });
});
it('refreshes selected compact task evidence and ignores an older read overtaken by a newer task update', async () => {
  let smoke = false;
  read.mockImplementation((command) => Promise.resolve(command.cmd === 'list_actions' ? { type: 'actions', actions: [] } : command.cmd === 'list_roles' ? { type: 'roles', roles: [] } : { ...detail(), task: { ...detail().task as object, verification_state: smoke ? 'passed' : 'pending', verification_summary: { manual_smoke_done: smoke } } }));
  const { store } = mountBoard(); await flush(); fireEvent.click(screen.getByRole('tab', { name: 'Verification' }));
  const reads = () => read.mock.calls.filter(([command]) => command.cmd === 'task_detail').length; const initial = reads();
  act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'task_upsert', id: 'other', updated_at: 'one' }] })); }); await flush(); expect(reads()).toBe(initial);
  smoke = true; act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 12, ops: [{ op: 'task_upsert', id: 'one', updated_at: 'two', verification_state: 'passed' }] })); }); await flush();
  expect(reads()).toBe(initial + 1); expect(screen.getByRole('checkbox', { name: 'Manual smoke done' })).toBeChecked();
  const old = held(); read.mockReturnValueOnce(old.promise); act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 13, ops: [{ op: 'task_upsert', id: 'one', updated_at: 'three' }] })); }); await flush(); const obsolete = read.mock.calls.at(-1)![1];
  act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 14, ops: [{ op: 'task_upsert', id: 'one', updated_at: 'four' }] })); }); await flush(); expect(obsolete.aborted).toBe(true);
  old.resolve({ ...detail(), task: { ...detail().task as object, verification_summary: { manual_smoke_done: false } } }); await flush(); expect(screen.getByRole('checkbox', { name: 'Manual smoke done' })).toBeChecked();
});

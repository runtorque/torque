import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { AuxiliaryFrame, TorqueCommand } from '../../protocol';
import { readCommand } from '../../protocol/http';
import type * as Http from '../../protocol/http';
import { AskResponse } from './AskResponse';
vi.mock('../../protocol/http', async (original) => ({ ...await original<typeof Http>(), readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const worker = { id: 'worker', name: 'Worker', cell_type: 'agent', session_id: 'session', status: 'running', group: 'Foundation' };
const ask = { id: 'ask', task: 'Choose release', description: 'Reviewed options', group: 'Foundation', lane: 'Backlog', labels: ['torque:human'], parent_task_id: 'parent', reply_agent_id: 'worker' };
const parent = { id: 'parent', task: 'Release task', description: 'Full context', group: 'Foundation', agent_id: 'worker' };
const detail = (command: TorqueCommand): AuxiliaryFrame => ({ type: 'task_detail', id: command.id, task: command.id === 'parent' ? parent : { ...ask, id: command.id } });
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
function held() { let resolve!: (value: AuxiliaryFrame) => void; return { promise: new Promise<AuxiliaryFrame>((done) => { resolve = done; }), resolve: (value: AuxiliaryFrame) => resolve(value) }; }
function mount(closed = false) {
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { ask: { ...ask, lane: closed ? 'Done' : 'Backlog' }, parent, other: { ...ask, id: 'other', task: 'Other question' } }, agents: { worker } }));
  const show = (taskId: string) => <Provider store={store}><AskResponse taskId={taskId} send={vi.fn()} /></Provider>;
  const view = render(show('ask')); return { ...view, store, replace: () => view.rerender(show('other')) };
}
beforeEach(() => { read.mockReset(); vi.useFakeTimers(); }); afterEach(() => { cleanup(); vi.useRealTimers(); });
it.each(['ask', 'parent'])('bounds %s detail loading and retains the answer through retry and late response', async (target) => {
  const pending = held(); let hold = true; let signal: AbortSignal | undefined;
  read.mockImplementation((command, owner) => { if (command.id === target && hold) { signal = owner; return pending.promise; } return Promise.resolve(detail(command)); });
  mount(); await flush(); const answer = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Answer Choose release' }); fireEvent.change(answer, { target: { value: 'Keep my answer' } }); answer.focus(); answer.setSelectionRange(2, 7);
  await advance(15_001); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(signal?.aborted).toBe(true); expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeDisabled(); expect(answer).toHaveFocus(); expect([answer.selectionStart, answer.selectionEnd]).toEqual([2, 7]);
  hold = false; fireEvent.click(screen.getByRole('button', { name: 'Refresh question' })); await flush(); expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeEnabled(); expect(answer).toHaveValue('Keep my answer');
  pending.resolve({ type: 'task_detail', id: target, task: { ...ask, id: target, description: 'Obsolete content' } }); await flush(); expect(screen.queryByText('Obsolete content')).not.toBeInTheDocument();
});
it('does not read a hidden closed ask and cancels the in-flight read when it closes', async () => {
  const pending = held(); read.mockReturnValue(pending.promise); const closed = mount(true); await flush(); expect(read).not.toHaveBeenCalled(); closed.unmount();
  const { store } = mount(); await flush(); const signal = read.mock.calls[0]![1]; act(() => { store.dispatch(projectionActions.taskDetailReceived({ type: 'task_detail', id: 'ask', task: { lane: 'Done' } })); }); await flush(); expect(signal.aborted).toBe(true); expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); expect(read).toHaveBeenCalledTimes(1);
});
it('rejects malformed or mismatched details before enabling reply', async () => {
  read.mockResolvedValue({ type: 'unrelated', id: 'ask', task: { ...ask } }); mount(); await flush();
  expect(screen.getByRole('alert')).toHaveTextContent('Question detail'); expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeDisabled();
  read.mockResolvedValue({ type: 'task_detail', id: 'ask', task: { ...ask, id: 'other' } }); fireEvent.click(screen.getByRole('button', { name: 'Refresh question' })); await flush(); expect(screen.getByRole('alert')).toHaveTextContent('Question detail');
});
it('retains accepted question and parent descriptions through a failed compact reconnect', async () => {
  read.mockImplementation((command) => Promise.resolve(detail(command))); const { store } = mount(); await flush();
  fireEvent.click(screen.getByText('Parent: Release task')); const answer = screen.getByRole('textbox', { name: 'Answer Choose release' }); fireEvent.change(answer, { target: { value: 'Retain through reconnect' } }); answer.focus();
  read.mockReturnValue(new Promise(() => {})); act(() => { store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { ask: { ...ask, description: undefined }, parent: { ...parent, description: undefined } }, agents: { worker } })); store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); await advance(15_001);
  expect(screen.getByText('Reviewed options')).toBeVisible(); expect(screen.getByText('Full context')).toBeVisible(); expect(answer).toHaveValue('Retain through reconnect'); expect(answer).toHaveFocus();
});
it('releases an unknown delivery, requires a fresh read and explicitly retries the same answer without reconnect replay', async () => {
  const pending = held(); const writes: TorqueCommand[] = []; let signal: AbortSignal | undefined;
  read.mockImplementation((command, owner) => { if (command.cmd === 'task_detail') return Promise.resolve(detail(command)); writes.push(command); signal = owner; return writes.length === 1 ? pending.promise : Promise.resolve({ type: 'ok', command: 'resolve_ask', request_id: command.request_id, task_id: 'ask', agent_id: 'worker' }); });
  const { store } = mount(); await flush(); const answer = screen.getByRole('textbox', { name: 'Answer Choose release' }); fireEvent.change(answer, { target: { value: '  Reviewed answer  ' } }); fireEvent.click(screen.getByRole('button', { name: 'Resolve ask' })); await advance(30_001);
  expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(signal?.aborted).toBe(true); expect(answer).toHaveValue('  Reviewed answer  '); expect(answer).toHaveAttribute('readonly'); expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeDisabled(); expect(screen.getByRole('button', { name: 'Refresh question' })).toBeEnabled();
  pending.resolve({ type: 'ok', command: 'resolve_ask', request_id: writes[0]!.request_id }); await flush(); expect(screen.queryByText('Answer delivered.')).not.toBeInTheDocument();
  act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); await flush(); expect(writes).toHaveLength(1); expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Resolve ask' })); await flush(); expect(writes[1]).toEqual(writes[0]); expect(writes[1]!.answer).toBe('Reviewed answer'); expect(screen.getByText('Answer delivered.')).toBeVisible();
});
it('aborts delivery observation on replacement without clearing another question draft', async () => {
  const pending = held(); let signal: AbortSignal | undefined;
  read.mockImplementation((command, owner) => { if (command.cmd === 'task_detail') return Promise.resolve(detail(command)); signal = owner; return pending.promise; });
  const { replace } = mount(); await flush(); fireEvent.change(screen.getByRole('textbox', { name: 'Answer Choose release' }), { target: { value: 'Old answer' } }); fireEvent.click(screen.getByRole('button', { name: 'Resolve ask' })); replace(); await flush(); expect(signal?.aborted).toBe(true);
  const answer = screen.getByRole('textbox'); expect(answer).toHaveValue(''); fireEvent.change(answer, { target: { value: 'New answer' } }); pending.resolve({ type: 'ok', command: 'resolve_ask', request_id: 'late' }); await flush(); expect(answer).toHaveValue('New answer'); expect(screen.queryByText('Answer delivered.')).not.toBeInTheDocument();
});
it('does not treat a read started before the unknown outcome as a fresh delivery review', async () => {
  const pendingWrite = held(); const pendingRead = held(); let holdReads = false;
  read.mockImplementation((command) => command.cmd !== 'task_detail' ? pendingWrite.promise : holdReads ? pendingRead.promise : Promise.resolve(detail(command)));
  const { store } = mount(); await flush(); fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Reviewed answer' } }); fireEvent.click(screen.getByRole('button', { name: 'Resolve ask' })); await advance(20_000);
  holdReads = true; act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); await advance(10_001); holdReads = false; pendingRead.resolve(detail({ cmd: 'task_detail', id: 'ask' })); await flush();
  expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeDisabled(); fireEvent.click(screen.getByRole('button', { name: 'Refresh question' })); await flush(); expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeEnabled();
});
it('keeps an unknown answer frozen when a fresh read changes the reply target', async () => {
  let changed = false;
  read.mockImplementation((command) => command.cmd !== 'task_detail' ? new Promise(() => {}) : Promise.resolve(command.id === 'ask' && changed ? { type: 'task_detail', id: 'ask', task: { ...ask, reply_agent_id: 'different' } } : detail(command)));
  mount(); await flush(); fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Original target answer' } }); fireEvent.click(screen.getByRole('button', { name: 'Resolve ask' })); await advance(30_001);
  changed = true; fireEvent.click(screen.getByRole('button', { name: 'Refresh question' })); await flush(); expect(screen.getByText(/The reply target changed/)).toBeVisible(); expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeDisabled(); expect(screen.getByRole('textbox')).toHaveValue('Original target answer'); expect(screen.getByRole('textbox')).toHaveAttribute('readonly');
});
it.each([{ task_id: 'other' }, { delivery_state: 'buffered' }])('rejects a contradictory correlated acknowledgement %o', async (patch) => {
  read.mockImplementation((command) => Promise.resolve(command.cmd === 'task_detail' ? detail(command) : { type: 'ok', command: 'resolve_ask', request_id: command.request_id, ...patch }));
  mount(); await flush(); const answer = screen.getByRole('textbox'); fireEvent.change(answer, { target: { value: 'Keep this answer' } }); fireEvent.click(screen.getByRole('button', { name: 'Resolve ask' })); await flush();
  expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(answer).toHaveValue('Keep this answer'); expect(screen.queryByText('Answer delivered.')).not.toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeDisabled();
});

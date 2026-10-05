import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { beforeEach, expect, it, vi } from 'vitest';
import { useAppSelector } from '../../app/hooks';
import { connectionActions, createAppStore, projectionActions, selectAuxiliaryResponseState } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { readCommand } from '../../protocol/http';
import type { AuxiliaryFrame, UnknownRecord } from '../../protocol';
import { AgentDetailWorkspace } from './AgentDetailWorkspace';
import { toAgentViewModel } from './model';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
beforeEach(() => { read.mockReset(); });

function mount() {
  const store = createAppStore(); const send = vi.fn(() => true);
  const agent = toAgentViewModel('engineer', { id: 'engineer', name: 'Engineer', kind: 'engineer', group: 'Foundation', cell_type: 'agent', status: 'running' });
  let settings: UnknownRecord = { pending_question: 'Initial question', pending_note: 'Review note', pending_note_kind: 'note', paused: true };
  let entries: UnknownRecord[] = [{ id: 1, author_cell_id: 'engineer', entry_type: 'checkpoint', entry: 'Existing journal', timestamp: 1 }];
  const initial = { ...compactStateFixture, agents: { engineer: agent.raw }, board_tasks: {}, engineer_settings: { Foundation: settings }, engineer_journal: { engineer: entries } };
  store.dispatch(projectionActions.snapshotReceived(initial)); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(initial));
  read.mockImplementation((command) => Promise.resolve(command.cmd === 'get_group_settings'
    ? { type: 'group_settings', group: 'Foundation', settings: {}, engineer_settings: settings }
    : command.cmd === 'engineer_journal_snapshot'
      ? { type: 'engineer_journal_snapshot', group: 'Foundation', engineer_journal: { engineer: entries }, engineer_worklog: {} }
      : { type: 'engineer_session_map', group: 'Foundation', session_map: {} }));
  function Panel() { const responses = useAppSelector(selectAuxiliaryResponseState); return <AgentDetailWorkspace agent={agent} group="Foundation" responses={responses} tasks={{}} directMessages={[]} peerThreads={[]} digestSettings={{}} digestBufferStats={{}} digestSentEvents={[]} sendCommand={send} onUnavailable={() => {}} />; }
  render(<Provider store={store}><Panel /></Provider>);
  return { store, send, settings: (next: UnknownRecord) => { settings = next; }, entries: (next: UnknownRecord[]) => { entries = next; }, reconnect: () => { const { engineer_journal: _journal, ...compact } = initial; void _journal; store.dispatch(projectionActions.snapshotReceived(compact)); store.dispatch(connectionActions.snapshotAccepted(compact)); } };
}

it('clears answered/dismissed Engineer banners on the live settings delta without a manual refresh', async () => {
  const app = mount(); await screen.findByText('Initial question'); await screen.findByText('Review note');
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
  expect(app.send).toHaveBeenCalledWith({ cmd: 'engineer_resume', group: 'Foundation', engineer_id: 'engineer' });
  const next = { pending_question: '', pending_note: '', pending_note_kind: '', paused: false }; app.settings(next);
  act(() => { app.store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'engineer_settings_update', group: 'Foundation', ...next }] })); });
  await waitFor(() => expect(screen.queryByText('Initial question')).not.toBeInTheDocument());
  expect(screen.queryByText('Review note')).not.toBeInTheDocument();
});

it('applies journal deletion to the displayed Engineer journal without a manual refresh', async () => {
  const app = mount(); const existing = await screen.findAllByText('Existing journal');
  fireEvent.click(existing.find((node) => node.closest('summary'))!.closest('summary')!);
  fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
  expect(app.send).toHaveBeenCalledWith({ cmd: 'engineer_journal_delete', group: 'Foundation', entry_id: '1', author_cell_id: 'engineer' });
  app.entries([]);
  act(() => { app.store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'journal_delete', group: 'Foundation', author_cell_id: 'engineer', id: 1 }] })); });
  await waitFor(() => expect(screen.queryAllByText('Existing journal')).toHaveLength(0));
});

it('shows an appended Engineer journal entry while retaining an unrelated answer draft', async () => {
  const app = mount(); await screen.findAllByText('Existing journal');
  const answer = screen.getByPlaceholderText('Answer and resume delivery'); fireEvent.change(answer, { target: { value: 'Keep this answer draft' } }); answer.focus();
  const entry = { id: 2, author_cell_id: 'engineer', entry_type: 'checkpoint', entry: 'New live journal entry', timestamp: 2 }; app.entries([entry, { id: 1, author_cell_id: 'engineer', entry: 'Existing journal', timestamp: 1 }]);
  act(() => { app.store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'journal_append', group: 'Foundation', ...entry }] })); });
  await screen.findAllByText('New live journal entry'); expect(answer).toHaveValue('Keep this answer draft'); expect(answer).toHaveFocus();
});


it('retains hydrated journal entries through a compact reconnect and applies deletion while refresh is stalled', async () => {
  const app = mount(); await screen.findAllByText('Existing journal');
  const summary = screen.getAllByText('Existing journal').find((node) => node.closest('summary'))!.closest('summary')!;
  fireEvent.click(summary); summary.focus();
  read.mockImplementation(() => new Promise(() => {}));
  act(() => app.reconnect());
  expect(summary.closest('details')).toHaveAttribute('open'); expect(summary).toHaveFocus();
  const entry = { id: 2, author_cell_id: 'engineer', entry: 'Appended during resync', timestamp: 2 };
  act(() => { app.store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 12, ops: [{ op: 'journal_append', group: 'Foundation', ...entry }] })); });
  await screen.findAllByText('Appended during resync'); expect(screen.getAllByText('Existing journal')).toHaveLength(2);
  act(() => { app.store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 13, ops: [{ op: 'journal_delete', author_cell_id: 'engineer', id: 1 }] })); });
  expect(screen.queryAllByText('Existing journal')).toHaveLength(0); expect(screen.getAllByText('Appended during resync')).toHaveLength(2);
});

it('does not refetch for journal/settings deltas and keeps another author out of the selected journal', async () => {
  const app = mount(); await screen.findAllByText('Existing journal');
  const reads = read.mock.calls.length;
  act(() => { app.store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [
    { op: 'journal_append', group: 'Foundation', author_cell_id: 'other', id: 5, entry: 'Other journal' },
    { op: 'journal_append', group: 'Foundation', author_cell_id: 'engineer', id: 6, entry: 'Selected journal' },
    { op: 'engineer_settings_update', group: 'Elsewhere', pending_question: 'Other question' },
  ] })); });
  await screen.findAllByText('Selected journal'); expect(screen.queryByText('Other journal')).not.toBeInTheDocument(); expect(screen.queryByText('Other question')).not.toBeInTheDocument();
  expect(read).toHaveBeenCalledTimes(reads); expect(screen.getByText('Initial question')).toBeInTheDocument();
});


it('does not let a late journal read resurrect a deletion or erase a live append', async () => {
  const app = mount(); await screen.findAllByText('Existing journal');
  let release!: (value: AuxiliaryFrame) => void;
  read.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  await waitFor(() => expect(release).toBeTypeOf('function'));
  const entry = { id: 2, author_cell_id: 'engineer', entry: 'Arrived during read' }; app.entries([entry]);
  act(() => { app.store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 12, ops: [
    { op: 'journal_delete', author_cell_id: 'engineer', id: 1 },
    { op: 'journal_append', group: 'Foundation', ...entry },
  ] })); });
  await act(async () => { release({ type: 'engineer_journal_snapshot', group: 'Foundation', engineer_journal: { engineer: [{ id: 1, entry: 'Existing journal' }] }, engineer_worklog: {} }); await Promise.resolve(); });
  await screen.findAllByText('Arrived during read'); expect(screen.queryAllByText('Existing journal')).toHaveLength(0);
});

it('renders persisted completed deliveries even when the task is no longer on the Board', async () => {
  const app = mount(); await screen.findAllByText('Existing journal');
  act(() => { app.store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [
    { op: 'engineer_worklog_append', group: 'Foundation', entry: { id: 8, task_id: 'archived-task', task_title: 'Archived delivered task', agent_name: 'Original worker', agent_owned: true, started_at: 10 } },
  ] })); });
  fireEvent.click(screen.getByRole('tab', { name: 'Completed' }));
  await screen.findByText('Archived delivered task'); expect(screen.getByText('Original worker')).toBeInTheDocument(); expect(screen.getByText('Not on board')).toBeInTheDocument();
});


it('shows a new note and waits for its acknowledged dismissal while retaining an answer draft', async () => {
  const app = mount(); await screen.findByText('Initial question');
  const answer = screen.getByPlaceholderText<HTMLTextAreaElement>('Answer and resume delivery');
  fireEvent.change(answer, { target: { value: 'Unsent answer' } }); answer.focus(); answer.setSelectionRange(2, 5);
  act(() => { app.store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'engineer_settings_update', group: 'Foundation', pending_question: 'Initial question', pending_note: 'New live note', pending_note_kind: 'warning' }] })); });
  await screen.findByText('New live note'); expect(answer).toHaveFocus(); expect(answer.selectionStart).toBe(2); expect(answer.selectionEnd).toBe(5);
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss note' }));
  expect(app.send).toHaveBeenCalledWith({ cmd: 'engineer_dismiss_note', group: 'Foundation', engineer_id: 'engineer' }); expect(screen.getByText('New live note')).toBeInTheDocument();
  act(() => { app.store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 12, ops: [{ op: 'engineer_settings_update', group: 'Foundation', pending_question: 'Initial question', pending_note: '' }] })); });
  expect(screen.queryByText('New live note')).not.toBeInTheDocument(); expect(answer).toHaveValue('Unsent answer');
});

it('retains other hydrated journal authors and treats an explicit empty refresh as authoritative', () => {
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
  store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'engineer_journal_snapshot', group: 'Foundation', engineer_journal: { first: [{ id: 1 }], second: [{ id: 2 }] }, engineer_worklog: { Foundation: [{ id: 3 }] } }));
  store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'engineer_journal_snapshot', group: 'Elsewhere', engineer_journal: { second: [] }, engineer_worklog: { Elsewhere: [] } }));
  store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
  expect(store.getState().projection.data.engineer_journal).toEqual({ first: [{ id: 1 }], second: [] });
  expect(store.getState().projection.data.engineer_worklog).toEqual({ Foundation: [{ id: 3 }], Elsewhere: [] });
  store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, engineer_journal: {}, engineer_worklog: {} }));
  expect(store.getState().projection.data.engineer_journal).toEqual({}); expect(store.getState().projection.data.engineer_worklog).toEqual({});
});

it('honors the live created-agent restriction in the persisted group worklog', async () => {
  const app = mount(); await screen.findAllByText('Existing journal');
  act(() => { app.store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [
    { op: 'engineer_worklog_append', group: 'Foundation', entry: { id: 8, task_id: 'owned', task_title: 'Owned delivery', agent_owned: true, started_at: 10 } },
    { op: 'engineer_worklog_append', group: 'Foundation', entry: { id: 9, task_id: 'shared', task_title: 'Shared delivery', agent_owned: false, started_at: 20 } },
  ] })); });
  fireEvent.click(screen.getByRole('tab', { name: 'Completed' })); await screen.findByText('Owned delivery'); await screen.findByText('Shared delivery');
  act(() => { app.store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 12, ops: [{ op: 'engineer_settings_update', group: 'Foundation', restrict_to_created_agents: true }] })); });
  expect(screen.queryByText('Shared delivery')).not.toBeInTheDocument(); expect(screen.getByText('Owned delivery')).toBeInTheDocument();
});

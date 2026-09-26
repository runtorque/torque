import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions, workspaceUiActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { AuxiliaryFrame, TorqueCommand } from '../../protocol';
import { readCommand } from '../../protocol/http';
import type * as Http from '../../protocol/http';
import { BoardPanel } from './BoardPanel';
vi.mock('../../protocol/http', async (original) => ({ ...await original<typeof Http>(), readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
function held() { let resolve!: (frame: AuxiliaryFrame) => void; return { promise: new Promise<AuxiliaryFrame>((done) => { resolve = done; }), resolve: (frame: AuxiliaryFrame) => resolve(frame) }; }
const task = { id: 'task', task: 'Catalog task', description: 'Saved description', group: 'Foundation', lane: 'Backlog', action_name: 'Foundation/action', agent_template: 'Foundation-role', action_vars: { SCOPE: 'saved scope' } };
async function setup() {
  const commands: TorqueCommand[] = [];
  read.mockImplementation((command) => {
    commands.push(command);
    if (command.cmd === 'task_detail') return Promise.resolve({ type: 'task_detail', id: command.id, task: { ...task, id: command.id } });
    if (command.cmd === 'list_actions') return Promise.resolve({ type: 'actions', group: command.group, actions: [{ name: `${String(command.group)}/action`, vars: [{ name: 'SCOPE', default: 'all' }] }] });
    if (command.cmd === 'list_roles') return Promise.resolve({ type: 'roles', group: command.group, roles: [{ slug: `${String(command.group)}-role`, name: `${String(command.group)} role` }] });
    return Promise.resolve({ type: 'state', seq: 1, board_tasks: { task } });
  });
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, groups: { Foundation: [], Other: [] }, board_tasks: { task, second: { ...task, id: 'second', task: 'Second task' } } })); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'actions', group: 'Wrong', actions: [{ name: 'wrong-global/action' }] }));
  const send = vi.fn(() => true); const view = render(<Provider store={store}><BoardPanel group="Foundation" sendCommand={send} onCommandUnavailable={vi.fn()} /></Provider>); await flush();
  return { ...view, commands, store, send };
}
beforeEach(() => { read.mockReset(); vi.useFakeTimers(); }); afterEach(() => { cleanup(); vi.useRealTimers(); });
it('owns creation options locally, refreshes on reconnect and preserves the draft through timeout', async () => {
  const { store, commands, send } = await setup(); expect(commands).toHaveLength(0);
  act(() => { store.dispatch(workspaceUiActions.setCreateTaskDialogOpen(true)); }); await flush();
  expect(commands).toContainEqual({ cmd: 'list_actions', group: 'Foundation' }); expect(send).not.toHaveBeenCalledWith(expect.objectContaining({ cmd: 'list_actions' }));
  expect(screen.getByRole('option', { name: 'Foundation/action' })).toBeInTheDocument(); expect(screen.queryByRole('option', { name: 'wrong-global/action' })).not.toBeInTheDocument();
  const title = screen.getByLabelText('Title'); fireEvent.change(title, { target: { value: 'Retained catalog draft' } }); title.focus(); (title as HTMLInputElement).setSelectionRange(2, 7);
  const original = read.getMockImplementation()!; const pending = held(); let hold = true;
  read.mockImplementation((command, signal) => command.cmd === 'list_actions' && hold ? pending.promise : original(command, signal));
  act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'actions', group: 'Wrong', actions: [{ name: 'wrong-global/action' }] })); store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); });
  await act(async () => { await vi.advanceTimersByTimeAsync(15_001); }); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(screen.getByLabelText('Title')).toBe(title); expect(title).toHaveValue('Retained catalog draft'); expect(title).toHaveFocus();
  expect([(title as HTMLInputElement).selectionStart, (title as HTMLInputElement).selectionEnd]).toEqual([2, 7]); expect(screen.getByRole('option', { name: 'Foundation/action' })).toBeInTheDocument();
  hold = false; fireEvent.click(screen.getByRole('button', { name: 'Retry task options' })); await flush(); pending.resolve({ type: 'actions', group: 'Wrong', actions: [{ name: 'late/action' }] }); await flush(); expect(screen.queryByRole('option', { name: 'late/action' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' })); await flush(); const count = commands.length; act(() => { store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); }); await flush(); expect(commands).toHaveLength(count);
});
it('loads the edited target group, retains unavailable selections and stops hidden execution reads', async () => {
  const { store, commands } = await setup(); act(() => { store.dispatch(workspaceUiActions.setDetailTask('task')); }); await flush();
  expect(screen.getByLabelText('SCOPE')).toHaveValue('saved scope'); fireEvent.change(screen.getByRole('combobox', { name: 'Group' }), { target: { value: 'Other' } }); await flush();
  expect(commands).toContainEqual({ cmd: 'list_actions', group: 'Other' }); expect(screen.getByRole('option', { name: 'Other/action' })).toBeInTheDocument(); expect(screen.getByRole('option', { name: 'Foundation/action (unavailable)' })).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: 'Worker role' })).toHaveValue('Foundation-role');
  fireEvent.click(screen.getByRole('tab', { name: 'Evidence' })); const reads = commands.filter((command) => command.cmd.startsWith('list_')).length; act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); await flush(); expect(commands.filter((command) => command.cmd.startsWith('list_'))).toHaveLength(reads);
  fireEvent.click(screen.getByRole('tab', { name: 'Execution' })); await flush(); expect(commands.at(-1)).toEqual({ cmd: 'list_roles', group: 'Other' });
});
it('discovers batch actions on first open without requesting unused roles or global options', async () => {
  const { store, commands } = await setup(); act(() => { store.dispatch(workspaceUiActions.toggleSelectedTask({ id: 'task', additive: true })); store.dispatch(workspaceUiActions.toggleSelectedTask({ id: 'second', additive: true })); });
  fireEvent.click(screen.getByRole('button', { name: 'Batch edit' })); await flush(); const dialog = within(screen.getByRole('dialog', { name: 'Batch edit tasks' }));
  expect(commands).toEqual([{ cmd: 'list_actions', group: 'Foundation' }]); expect(dialog.getByRole('option', { name: 'Foundation/action' })).toBeInTheDocument(); expect(dialog.queryByRole('option', { name: 'wrong-global/action' })).not.toBeInTheDocument();
});
it('rejects a wrong-group catalog and retries while keeping the editable draft', async () => {
  const { store } = await setup(); const original = read.getMockImplementation()!;
  read.mockImplementation((command, signal) => command.cmd === 'list_actions' ? Promise.resolve({ type: 'actions', group: 'Wrong', actions: [{ name: 'wrong/action' }] }) : original(command, signal));
  act(() => { store.dispatch(workspaceUiActions.setCreateTaskDialogOpen(true)); }); await flush();
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Keep reviewed title' } }); expect(screen.getByRole('alert')).toHaveTextContent('invalid response'); expect(screen.queryByRole('option', { name: 'wrong/action' })).not.toBeInTheDocument();
  read.mockImplementation(original); fireEvent.click(screen.getByRole('button', { name: 'Retry task options' })); await flush(); expect(screen.getByLabelText('Title')).toHaveValue('Keep reviewed title'); expect(screen.getByRole('option', { name: 'Foundation/action' })).toBeInTheDocument();
});
it('retains accepted options when a refresh contains malformed entries', async () => {
  const { store } = await setup(); act(() => { store.dispatch(workspaceUiActions.setCreateTaskDialogOpen(true)); }); await flush();
  fireEvent.change(screen.getByRole('combobox', { name: 'Action' }), { target: { value: 'Foundation/action' } });
  fireEvent.change(screen.getByLabelText('SCOPE'), { target: { value: 'Reviewed variables' } });
  const original = read.getMockImplementation()!;
  read.mockImplementation((command, signal) => command.cmd === 'list_actions' ? Promise.resolve({ type: 'actions', group: 'Foundation', actions: [{ name: 'replacement/action' }, { name: '' }] }) : original(command, signal));
  act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); await flush();
  expect(screen.getByRole('alert')).toHaveTextContent('invalid catalog entries');
  expect(screen.getByRole('combobox', { name: 'Action' })).toHaveValue('Foundation/action'); expect(screen.getByLabelText('SCOPE')).toHaveValue('Reviewed variables');
  expect(screen.queryByRole('option', { name: 'replacement/action' })).not.toBeInTheDocument();
  read.mockImplementation((command, signal) => command.cmd === 'list_actions' ? Promise.resolve({ type: 'actions', group: 'Foundation', actions: [] }) : original(command, signal));
  fireEvent.click(screen.getByRole('button', { name: 'Retry task options' })); await flush();
  expect(screen.getByRole('option', { name: 'Foundation/action (unavailable)' })).toBeInTheDocument(); expect(screen.getByRole('combobox', { name: 'Action' })).toHaveValue('Foundation/action');
});
it('cancels the old target-group request and ignores its late catalog', async () => {
  const { store } = await setup(); const original = read.getMockImplementation()!; const pending = held(); let signal: AbortSignal | undefined;
  read.mockImplementation((command, owner) => { if (command.cmd === 'list_actions' && command.group === 'Foundation') { signal = owner; return pending.promise; } return original(command, owner); });
  act(() => { store.dispatch(workspaceUiActions.setDetailTask('task')); }); await flush();
  fireEvent.change(screen.getByRole('combobox', { name: 'Group' }), { target: { value: 'Other' } }); await flush(); expect(signal?.aborted).toBe(true);
  expect(screen.getByRole('option', { name: 'Other/action' })).toBeInTheDocument(); pending.resolve({ type: 'actions', group: 'Foundation', actions: [{ name: 'late/action' }] }); await flush();
  expect(screen.queryByRole('option', { name: 'late/action' })).not.toBeInTheDocument(); expect(screen.getByRole('option', { name: 'Other/action' })).toBeInTheDocument(); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { HistoryPanel } from './HistoryPanel';

afterEach(() => vi.unstubAllGlobals());
function setup() {
  const commands: TorqueCommand[] = []; const signals: AbortSignal[] = [];
  let failure = ''; let mismatch = false; let revision = 0;
  const pending: { command: TorqueCommand; resolve: (value: unknown) => void }[] = []; let hold = false;
  const rows = [{ id: 'run-1', name: 'Worker One', group: 'Foundation', status: 'merged', kind: 'worker' }, { id: 'run-2', name: 'Worker Two', group: 'Foundation', status: 'active', kind: 'engineer' }];
  const frame = (command: TorqueCommand): UnknownRecord => {
    if (command.cmd === failure) return { type: 'error', message: 'Injected history refusal' };
    if (command.cmd === 'get_agent_history') return { type: 'agent_history_list', records: rows.filter((row) => !command.status || row.status === command.status) };
    return { type: 'agent_history_detail', record: { ...rows.find((row) => row.id === command.agent_id), id: mismatch ? 'wrong' : command.agent_id, template: 'worker-role', total_tokens_in: 42, total_tokens_out: 17, worktree_branch: `branch-${revision}` }, tasks: [{ id: 1, task_id: 'linked', task_title: 'Actual stored task title', outcome: 'done', started_at: 100 }, { id: 2, task_id: 'missing', task_title: 'Deleted task' }], messages: [{ id: 1, message: `Persisted message ${revision}`, task_id: 'linked' }] };
  };
  const response = (command: TorqueCommand) => ({ ok: true, json: () => Promise.resolve({ ok: true, data: frame(command) }) });
  vi.stubGlobal('fetch', vi.fn((_url: string, options?: RequestInit) => {
    const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand; commands.push(command); signals.push(options?.signal as AbortSignal);
    return hold ? new Promise((resolve) => pending.push({ command, resolve })) : Promise.resolve(response(command));
  }));
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { linked: { id: 'linked', task: 'Linked task', group: 'Other' } } })); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  const send = vi.fn(); const view = render(<Provider store={store}><HistoryPanel group="Foundation" send={send} /></Provider>);
  const reconnect = async () => { await act(async () => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); await Promise.resolve(); }); };
  return { ...view, store, send, commands, signals, reconnect, fail: (value: string) => { failure = value; }, mismatch: (value: boolean) => { mismatch = value; }, remote: () => { revision += 1; }, hold: () => { hold = true; }, release: async (index: number) => { await act(async () => { const item = pending[index]!; item.resolve(response(item.command)); await Promise.resolve(); }); } };
}

it('refreshes the current status and selected run while retaining search, DOM, focus, caret and scroll', async () => {
  const test = setup(); fireEvent.click(await screen.findByRole('button', { name: /Worker One/ })); await screen.findByText('Persisted message 0');
  const search = screen.getByRole<HTMLInputElement>('textbox', { name: 'Search history' });
  fireEvent.change(search, { target: { value: 'Worker' } }); search.focus(); search.setSelectionRange(1, 4);
  const detail = screen.getByRole('region', { name: 'Run detail' }); detail.scrollTop = 128;
  test.remote(); await test.reconnect(); await screen.findByText('Persisted message 1');
  expect(test.commands.slice(-2)).toEqual([{ cmd: 'get_agent_history', status: 'merged', limit: 100 }, { cmd: 'get_agent_history_detail', agent_id: 'run-1', message_limit: 100 }]);
  expect(screen.getByRole('textbox', { name: 'Search history' })).toBe(search); expect(search).toHaveFocus(); expect(search).toHaveValue('Worker'); expect([search.selectionStart, search.selectionEnd]).toEqual([1, 4]); expect(detail.scrollTop).toBe(128);
  expect(screen.getByRole('button', { name: /Worker One/ })).toHaveAttribute('aria-current', 'true');
  fireEvent.click(screen.getByRole('button', { name: 'Active' })); await screen.findByRole('button', { name: /Worker Two/ });
  await test.reconnect(); expect(test.commands.at(-2)).toMatchObject({ status: 'active' }); expect(screen.getByText('Persisted message 1')).toBeVisible();
});

it('retains prior results and detail after errors, rejects wrong detail and retries independently', async () => {
  const test = setup(); fireEvent.click(await screen.findByRole('button', { name: /Worker One/ })); await screen.findByText('Persisted message 0');
  test.fail('get_agent_history'); test.remote(); await test.reconnect(); await screen.findByText(/History refresh failed/); await screen.findByText('Persisted message 1');
  expect(screen.getByRole('button', { name: /Worker One/ })).toBeVisible(); test.fail(''); fireEvent.click(screen.getByRole('button', { name: 'Retry history' })); await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  test.mismatch(true); test.remote(); await test.reconnect(); await screen.findByText(/Could not load the selected run/); expect(screen.getByText('Persisted message 1')).toBeVisible(); expect(screen.queryByText('Persisted message 2')).not.toBeInTheDocument();
  test.mismatch(false); const before = test.commands.length; fireEvent.click(screen.getByRole('button', { name: 'Retry run' })); await screen.findByText('Persisted message 2'); expect(test.commands.slice(before)).toEqual([{ cmd: 'get_agent_history_detail', agent_id: 'run-1', message_limit: 100 }]);
});

it('cancels stale selection/status reads and all hidden reads even if a fetch ignores abort', async () => {
  const test = setup(); await screen.findByRole('button', { name: /Worker One/ });
  fireEvent.click(screen.getByRole('button', { name: 'All' })); await screen.findByRole('button', { name: /Worker Two/ });
  test.hold(); fireEvent.click(screen.getByRole('button', { name: /Worker One/ })); fireEvent.click(screen.getByRole('button', { name: /Worker Two/ }));
  expect(test.signals.at(-2)?.aborted).toBe(true); await test.release(1); await screen.findByRole('heading', { name: 'Worker Two' }); await test.release(0); expect(screen.queryByRole('heading', { name: 'Worker One' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Active' })); fireEvent.click(screen.getByRole('button', { name: 'Removed' })); expect(test.signals.at(-2)?.aborted).toBe(true);
  await test.release(3); await screen.findByText('No historical runs'); await test.release(2); expect(screen.getByText('No historical runs')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' })); const count = test.commands.length; test.unmount(); expect(test.signals.slice(-2).every((signal) => signal.aborted)).toBe(true); await test.reconnect(); expect(test.commands).toHaveLength(count); await test.release(4); await test.release(5);
});

it('renders persisted role/tokens/task fields and opens known task and message targets across groups', async () => {
  const test = setup(); fireEvent.click(await screen.findByRole('button', { name: /Worker One/ })); await screen.findByText('Actual stored task title');
  expect(screen.getByText('42 in / 17 out')).toBeVisible(); expect(screen.getByText('worker-role')).toBeVisible(); expect(screen.getByText('done')).toBeVisible();
  const open = screen.getAllByRole('button', { name: 'Open task' }); expect(open[1]).toBeDisabled(); fireEvent.click(open[0]!); expect(test.send).toHaveBeenCalledWith({ cmd: 'ui_select_group', group: 'Other' }); expect(test.store.getState().workspaceUi).toMatchObject({ activePanel: 'board', detailTaskId: 'linked' });
  fireEvent.click(screen.getByRole('button', { name: 'Open message task' })); expect(test.store.getState().workspaceUi.detailTaskId).toBe('linked');
  fireEvent.click(screen.getByRole('button', { name: 'Active' })); fireEvent.click(await screen.findByRole('button', { name: /Worker Two/ })); fireEvent.click(await screen.findByRole('button', { name: 'Focus live agent' })); expect(test.send).toHaveBeenCalledWith({ cmd: 'focus_agent', id: 'run-2' }); expect(test.store.getState().workspaceUi).toMatchObject({ activePanel: 'agents', selectedAgentId: 'run-2' });
});

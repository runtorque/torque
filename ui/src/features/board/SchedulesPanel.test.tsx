import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions, selectTasksState } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { readCommand } from '../../protocol/http';
import type * as Http from '../../protocol/http';
import type { AuxiliaryFrame, TorqueCommand, UnknownRecord } from '../../protocol';
import { localSchedule } from './taskEditModel';
import { SchedulesPanel } from './SchedulesPanel';
vi.mock('../../protocol/http', async (original) => ({ ...await original<typeof Http>(), readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const initial: UnknownRecord[] = [
  { id: 'z', name: 'Zulu schedule', group: 'Foundation', slug: 'zulu', task_template: 'Review {date}', action_name: 'review', action_vars: { SCOPE: 'saved scope' }, cron_expr: '0 9 * * *', timezone: 'UTC', next_run_at: '2035-01-02T09:00:00Z', enabled: true, run_count: 3 },
  { id: 'a', name: 'Alpha schedule', group: 'Other', slug: 'alpha', task_template: 'One-time work', scheduled_at: '2035-01-02T15:37:42Z', timezone: 'America/Sao_Paulo', enabled: false, run_count: 0 },
];
const actions = [{ name: 'review', vars: [{ name: 'TASK' }, { name: 'torque' }, { name: 'SCOPE', default: 'all' }] }, { name: 'test', vars: [{ name: 'SUITE', default: 'unit' }] }];
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
function held<T>() { let resolve!: (value: T) => void; return { promise: new Promise<T>((done) => { resolve = done; }), resolve: (value: T) => resolve(value) }; }
async function setup() {
  let rows = structuredClone(initial); const commands: TorqueCommand[] = [];
  read.mockImplementation((command) => {
    commands.push(command);
    if (command.cmd === 'list_actions') return Promise.resolve({ type: 'actions', group: command.group, actions });
    if (command.cmd === 'list_roles') return Promise.resolve({ type: 'roles', roles: [{ slug: 'reviewer', name: 'Reviewer' }] });
    if (command.cmd === 'schedule_list') return Promise.resolve({ type: 'schedule_list', schedules: rows });
    if (command.cmd === 'schedule_create') { rows = [...rows, { ...command, id: 'new' }]; return Promise.resolve({ type: 'ok', schedule_id: 'new' }); }
    if (command.cmd === 'schedule_update') rows = rows.map((row) => row.id === command.id ? { ...row, ...command } : row);
    if (command.cmd === 'schedule_remove') rows = rows.filter((row) => row.id !== command.id);
    if (command.cmd === 'schedule_run') return Promise.resolve({ type: 'ok', task_id: 'QA:1' });
    return Promise.resolve({ type: 'state', seq: 1, schedules: Object.fromEntries(rows.map((row) => [String(row.id), row])) });
  });
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, groups: { Foundation: [], Other: [] }, schedules: Object.fromEntries(rows.map((row) => [String(row.id), row])) })); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  const view = render(<Provider store={store}><SchedulesPanel group="Foundation" onClose={vi.fn()} /></Provider>); await flush();
  return { ...view, commands, store };
}
beforeEach(() => { read.mockReset(); }); afterEach(() => { cleanup(); vi.useRealTimers(); });
it('lists all groups in name order with operational metadata and a group filter', async () => {
  await setup(); expect(screen.getAllByRole('article').map((node) => node.getAttribute('aria-label'))).toEqual(['Alpha schedule', 'Zulu schedule']);
  const row = within(screen.getByRole('article', { name: 'Zulu schedule' })); expect(row.getByText(/zulu · Foundation · Enabled/)).toBeVisible(); expect(row.getByText('Review {date}')).toBeVisible(); expect(row.getByText('Action: review')).toBeVisible(); expect(row.getByText(/Recurring.*Timezone: UTC/)).toBeVisible(); expect(row.getByText(/Next:.*UTC.*3 runs/)).toBeVisible();
  const next = new Date('2035-01-02T09:00:00Z').toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' }); expect(row.getByText(`Next: ${next} · 3 runs`)).toBeVisible();
  fireEvent.change(screen.getByLabelText('Show schedules'), { target: { value: 'Foundation' } }); expect(screen.getAllByRole('article')).toHaveLength(1);
});
it('offers all presets, retains trigger drafts and sends only the active trigger', async () => {
  const { commands } = await setup(); fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'New recurring' } });
  for (const [label, value] of [['Every 30m', '*/30 * * * *'], ['Hourly', '0 * * * *'], ['Daily 9am', '0 9 * * *'], ['Weekdays', '0 9 * * 1-5'], ['Weekly', '0 9 * * 1'], ['Monthly', '0 9 1 * *']] as const) { fireEvent.click(screen.getByRole('button', { name: label })); expect(screen.getByLabelText('Cron')).toHaveValue(value); }
  fireEvent.click(screen.getByRole('button', { name: 'One-time' })); fireEvent.change(screen.getByLabelText('Date & time'), { target: { value: '2035-02-03T10:30' } });
  fireEvent.click(screen.getByRole('button', { name: 'Recurring' })); expect(screen.getByLabelText('Cron')).toHaveValue('0 9 1 * *');
  fireEvent.click(screen.getByRole('button', { name: 'One-time' })); expect(screen.getByLabelText('Date & time')).toHaveValue('2035-02-03T10:30'); fireEvent.click(screen.getByRole('button', { name: 'Recurring' }));
  fireEvent.click(screen.getByRole('button', { name: 'Create schedule' })); await flush();
  expect(commands.find((cmd) => cmd.cmd === 'schedule_create')).toMatchObject({ cron_expr: '0 9 1 * *', scheduled_at: '' });
});
it('edits named variables with per-action drafts and reloads catalogs for reassignment', async () => {
  const { commands } = await setup(); fireEvent.click(within(screen.getByRole('article', { name: 'Zulu schedule' })).getByRole('button', { name: 'Edit' })); await flush();
  expect(screen.getByLabelText('SCOPE')).toHaveValue('saved scope'); expect(screen.queryByLabelText('TASK')).not.toBeInTheDocument(); expect(screen.queryByLabelText('torque')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('SCOPE'), { target: { value: 'reviewed scope' } }); fireEvent.change(screen.getByLabelText('Action'), { target: { value: 'test' } }); expect(screen.getByLabelText('SUITE')).toHaveValue('unit');
  fireEvent.change(screen.getByLabelText('Action'), { target: { value: 'review' } }); expect(screen.getByLabelText('SCOPE')).toHaveValue('reviewed scope');
  fireEvent.change(screen.getByLabelText('Group'), { target: { value: 'Other' } }); await flush(); expect(commands).toContainEqual({ cmd: 'list_actions', group: 'Other' });
  fireEvent.click(screen.getByRole('button', { name: 'Save schedule' })); await flush(); expect(commands.find((cmd) => cmd.cmd === 'schedule_update')).toMatchObject({ id: 'z', group: 'Other', action_vars: { SCOPE: 'reviewed scope' } });
});
it('preserves a one-time instant including seconds when editing unrelated fields', async () => {
  const { commands } = await setup(); fireEvent.click(within(screen.getByRole('article', { name: 'Alpha schedule' })).getByRole('button', { name: 'Edit' })); await flush();
  expect(screen.getByLabelText('Date & time')).toHaveValue(localSchedule('2035-01-02T15:37:42Z'));
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Renamed one-time' } }); fireEvent.click(screen.getByRole('button', { name: 'Save schedule' })); await flush();
  expect(commands.find((cmd) => cmd.cmd === 'schedule_update')).toMatchObject({ cron_expr: '', scheduled_at: '2035-01-02T15:37:42Z' });
});
it('requires confirmation and keeps the reviewed deletion identity through live changes', async () => {
  const { commands, store } = await setup(); const row = within(screen.getByRole('article', { name: 'Zulu schedule' })); fireEvent.click(row.getByRole('button', { name: 'Remove' }));
  expect(screen.getByRole('dialog', { name: 'Remove schedule' })).toHaveTextContent('Zulu schedule'); fireEvent.click(screen.getByRole('button', { name: 'Cancel removal' })); expect(commands.filter((cmd) => cmd.cmd === 'schedule_remove')).toHaveLength(0);
  fireEvent.click(row.getByRole('button', { name: 'Remove' })); act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'schedule_list', schedules: [{ ...initial[1], name: 'Replacement' }] })); });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm removal' })); await flush(); expect(commands.filter((cmd) => cmd.cmd === 'schedule_remove')).toEqual([{ cmd: 'schedule_remove', id: 'z' }]);
});
it('retains exact creation intent through an observation timeout and retries the same key', async () => {
  vi.useFakeTimers(); const { commands } = await setup(); const pending = held<AuxiliaryFrame>(); const original = read.getMockImplementation()!; let first = true;
  read.mockImplementation((command, signal) => { if (command.cmd === 'schedule_create' && first) { first = false; commands.push(command); return pending.promise; } return original(command, signal); });
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Unknown creation' } }); fireEvent.click(screen.getByRole('button', { name: 'Hourly' })); fireEvent.click(screen.getByRole('button', { name: 'Create schedule' }));
  await act(async () => { await vi.advanceTimersByTimeAsync(30_001); }); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(screen.getByLabelText('Name')).toBeDisabled(); expect(screen.getByRole('button', { name: 'Close' })).toBeDisabled(); expect(within(screen.getByRole('article', { name: 'Zulu schedule' })).getByRole('button', { name: 'Edit' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Retry schedule creation' })); await flush(); const writes = commands.filter((cmd) => cmd.cmd === 'schedule_create'); expect(writes).toHaveLength(2); expect(writes[1]).toEqual(writes[0]);
  pending.resolve({ type: 'ok', schedule_id: 'obsolete' }); await flush(); expect(screen.getByLabelText('Name')).toHaveValue('');
});
it('retains the editor through catalog deadline and retries without accepting the late response', async () => {
  vi.useFakeTimers(); const { store } = await setup(); const pending = held<AuxiliaryFrame>(); const original = read.getMockImplementation()!; let hold = true;
  read.mockImplementation((cmd, signal) => cmd.cmd === 'list_actions' && hold ? pending.promise : original(cmd, signal));
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Retained draft' } }); act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); });
  await act(async () => { await vi.advanceTimersByTimeAsync(15_001); }); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(screen.getByLabelText('Name')).toHaveValue('Retained draft');
  hold = false; fireEvent.click(screen.getByRole('button', { name: 'Retry schedule options' })); await flush(); pending.resolve({ type: 'actions', group: 'Other', actions: [{ name: 'obsolete' }] }); await flush(); expect(screen.queryByRole('option', { name: 'obsolete' })).not.toBeInTheDocument();
});
it('keeps failed edits and refuses unrelated write acknowledgements', async () => {
  const { commands } = await setup(); const original = read.getMockImplementation()!;
  read.mockImplementation((command, signal) => command.cmd === 'schedule_update' ? Promise.resolve({ type: 'state', seq: 2, schedules: { other: { id: 'other' } } }) : original(command, signal));
  fireEvent.click(within(screen.getByRole('article', { name: 'Zulu schedule' })).getByRole('button', { name: 'Edit' })); await flush();
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Retained update' } }); fireEvent.click(screen.getByRole('button', { name: 'Save schedule' })); await flush();
  expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(screen.getByLabelText('Name')).toHaveValue('Retained update');
  read.mockImplementation(original); fireEvent.click(screen.getByRole('button', { name: 'Save schedule' })); await flush(); expect(commands).toContainEqual(expect.objectContaining({ cmd: 'schedule_update', name: 'Retained update' })); expect(screen.getByLabelText('Name')).toHaveValue('');
});
it('keeps known schedules through a list deadline and accepts only the explicit retry', async () => {
  vi.useFakeTimers(); const { store } = await setup(); const original = read.getMockImplementation()!; const pending = held<AuxiliaryFrame>(); let hold = true;
  read.mockImplementation((command, signal) => command.cmd === 'schedule_list' && hold ? pending.promise : original(command, signal));
  act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); await act(async () => { await vi.advanceTimersByTimeAsync(15_001); });
  expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(screen.getAllByRole('article')).toHaveLength(2);
  hold = false; fireEvent.click(screen.getByRole('button', { name: 'Retry schedules' })); await flush(); pending.resolve({ type: 'schedule_list', schedules: [] }); await flush(); expect(screen.getAllByRole('article')).toHaveLength(2);
});
it('cancels reads when the schedule surface unmounts and ignores obsolete catalog responses', async () => {
  const { unmount, store } = await setup(); const pending = held<AuxiliaryFrame>(); const signals: AbortSignal[] = [];
  read.mockImplementation((_command, signal) => { signals.push(signal); return pending.promise; });
  act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); expect(signals).toHaveLength(3); unmount(); expect(signals.every((signal) => signal.aborted)).toBe(true);
  pending.resolve({ type: 'schedule_list', schedules: [] }); await flush(); expect(Object.keys(selectTasksState(store.getState()).schedules)).toHaveLength(2);
});

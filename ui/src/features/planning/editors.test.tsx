import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useAppSelector } from '../../app/hooks';
import { connectionActions, createAppStore, projectionActions, selectPlanningState } from '../../app/store';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { compactStateFixture } from '../../protocol/fixtures';
import { DecisionEditor, InitiativeEditor } from './PlanningEditors';
import { PlanningWorkspace } from './PlanningWorkspace';

const initiative = { id: 'i', group_name: 'Foundation', title: 'Roadmap', planning_status: 'parked', summary: 'Full summary', why: 'Original why', links: { tasks: [], decisions: [] } };
const decision = { id: 'd', architect_id: 'a', title: 'Decision', rationale: 'Full rationale', status: 'revised', linked_task_ids: ['t'], linked_engineer_ids: ['e'] };
const tasks = [{ id: 't', task: 'Linked task' }]; const engineers = [{ id: 'e', name: 'Engineer' }];
afterEach(() => vi.unstubAllGlobals());
function setup(kind: 'initiative' | 'decision') {
  const store = createAppStore(); const calls: TorqueCommand[] = []; const onClose = vi.fn();
  store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  let record: UnknownRecord = structuredClone(kind === 'initiative' ? initiative : decision); let fail = '';
  store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, initiatives: { i: { id: 'i', title: 'Roadmap' } }, decisions: { d: { id: 'd', title: 'Decision', architect_id: 'a' } } }));
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
    const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; calls.push(command);
    if (command.cmd === fail) return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: false, creation_refused: command.cmd === 'board_add_task', error: 'Write rejected' }) });
    let data: UnknownRecord;
    if (command.cmd === 'initiative_show') data = { ...record, type: 'initiative' };
    else if (command.cmd === 'list_actions') data = { type: 'actions', actions: [] };
    else if (command.cmd === 'list_roles') data = { type: 'roles', roles: [] };
    else if (command.cmd === 'board_add_task') data = { type: 'board_task_added', task_id: 'new-task', title: command.task };
    else if (command.cmd === 'decisions_snapshot') data = { type: 'decisions_snapshot', decisions: { d: record } };
    else if (command.cmd === 'initiative_link_task' || command.cmd === 'initiative_unlink_task') {
      record = { ...record, links: { tasks: command.cmd === 'initiative_link_task' ? [command.task_id] : [], decisions: [] } };
      data = command.cmd === 'initiative_link_task' ? { type: 'initiative_task_linked', link: { initiative_id: record.id, link_type: 'task', target_id: command.task_id } } : { type: 'initiative_task_unlinked', removed: true };
    } else {
      record = { ...record, ...command, ...(command.cmd === 'initiative_archive' ? { archived: true } : {}) };
      data = kind === 'initiative' ? { type: command.cmd === 'initiative_archive' ? 'initiative_archived' : 'initiative_updated', initiative: record } : { ...record, type: 'ok' };
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data }) });
  }));
  function Harness() {
    const planning = useAppSelector(selectPlanningState);
    return kind === 'initiative' ? <InitiativeEditor item={planning.initiatives.i as UnknownRecord ?? initiative} tasks={tasks} decisions={[]} onClose={onClose} /> : <DecisionEditor item={planning.decisions.d as UnknownRecord ?? decision} tasks={tasks} engineers={engineers} decisions={[decision]} onClose={onClose} />;
  }
  render(<Provider store={store}><Harness /></Provider>);
  return { store, calls, onClose, fail: (cmd: string) => { fail = cmd; } };
}
describe('Planning editor acknowledgements and contracts', () => {
  it('hydrates initiative fields and links without replacing a local draft or caret', async () => {
    const { calls, store, onClose, fail } = setup('initiative');
    await waitFor(() => expect(screen.getByLabelText('Summary')).toHaveValue('Full summary'));
    expect(screen.getByRole('combobox', { name: 'Status' })).toHaveValue('parked');
    expect(screen.queryByRole('option', { name: 'done' })).not.toBeInTheDocument();
    const why = screen.getByLabelText<HTMLTextAreaElement>('Why this matters');
    fireEvent.change(why, { target: { value: 'Keep draft' } }); why.focus(); why.setSelectionRange(2, 4);
    act(() => { store.dispatch(connectionActions.connected({ at: 1, reconnect: true })); });
    await waitFor(() => expect(calls.filter((call) => call.cmd === 'initiative_show').length).toBeGreaterThanOrEqual(2));
    expect(why).toHaveValue('Keep draft'); expect(why).toHaveFocus(); expect(why.selectionStart).toBe(2);
    fireEvent.change(screen.getByLabelText('Linked record'), { target: { value: 't' } });
    fail('initiative_link_task'); fireEvent.click(screen.getByRole('button', { name: 'Link' }));
    await screen.findByText('Write rejected'); expect(screen.getByLabelText('Linked record')).toHaveValue('t');
    fail(''); fireEvent.click(screen.getByRole('button', { name: 'Link' })); await screen.findByRole('button', { name: 'Unlink' });
    expect(why).toHaveValue('Keep draft');
    fireEvent.click(screen.getByRole('button', { name: 'Unlink' })); await waitFor(() => expect(screen.queryByRole('button', { name: 'Unlink' })).not.toBeInTheDocument());
    fail('initiative_update'); fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Write rejected'); expect(onClose).not.toHaveBeenCalled();
    fail(''); fireEvent.click(screen.getByRole('button', { name: 'Save' })); await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(calls.find((call) => call.cmd === 'initiative_update')).toEqual({ cmd: 'initiative_update', id: 'i', why: 'Keep draft' });
  });
  it('reviews the current Initiative draft and retries a failed task link without creating twice', async () => {
    const { calls, fail, onClose } = setup('initiative');
    await waitFor(() => expect(screen.getByLabelText('Summary')).toHaveValue('Full summary'));
    fireEvent.change(screen.getByLabelText('Why this matters'), { target: { value: 'Unsaved rationale' } });
    fail('list_actions');
    fireEvent.click(screen.getByRole('button', { name: 'Create Board task' }));
    let dialog = within(screen.getByRole('dialog', { name: 'Create Board task' }));
    await dialog.findByText('Write rejected');
    fail(''); fireEvent.click(dialog.getByRole('button', { name: 'Retry task options' }));
    await waitFor(() => expect(dialog.queryByText('Write rejected')).not.toBeInTheDocument());
    expect(dialog.getByLabelText('Title')).toHaveValue('Roadmap');
    expect(dialog.getByLabelText('Description')).toHaveValue('Source initiative: i — Roadmap\n\nSummary\nFull summary\n\nWhy\nUnsaved rationale');
    expect(dialog.getByLabelText('Lane')).toHaveValue('');
    fireEvent.click(dialog.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByLabelText('Why this matters')).toHaveValue('Unsaved rationale');
    expect(calls.some((call) => call.cmd === 'board_add_task')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Create Board task' }));
    dialog = within(screen.getByRole('dialog', { name: 'Create Board task' }));
    fireEvent.change(dialog.getByLabelText('Title'), { target: { value: 'Reviewed task' } });
    fail('board_add_task'); fireEvent.click(dialog.getByRole('button', { name: 'Create task' }));
    await dialog.findByText('Write rejected'); expect(dialog.getByLabelText('Title')).toHaveValue('Reviewed task');
    expect(calls.some((call) => call.cmd === 'initiative_link_task')).toBe(false);
    fail('initiative_link_task'); fireEvent.click(dialog.getByRole('button', { name: 'Create task' }));
    await dialog.findByRole('button', { name: 'Retry link' });
    expect(dialog.getByLabelText('Title')).toBeDisabled();
    fireEvent.click(dialog.getByRole('button', { name: 'Close' }));
    expect(screen.getByLabelText('Why this matters')).toHaveValue('Unsaved rationale');
    fireEvent.click(screen.getByRole('button', { name: 'Resume task link' }));
    dialog = within(screen.getByRole('dialog', { name: 'Create Board task' }));
    fail(''); fireEvent.click(dialog.getByRole('button', { name: 'Retry link' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Create Board task' })).not.toBeInTheDocument());
    await screen.findByRole('button', { name: 'Unlink' });
    expect(screen.getByLabelText('Why this matters')).toHaveValue('Unsaved rationale');
    expect(calls.filter((call) => call.cmd === 'board_add_task')).toHaveLength(2); // one rejected, one acknowledged
    expect(calls.filter((call) => call.cmd === 'initiative_link_task')).toEqual([
      { cmd: 'initiative_link_task', id: 'i', task_id: 'new-task' }, { cmd: 'initiative_link_task', id: 'i', task_id: 'new-task' },
    ]);
    expect(calls.some((call) => call.cmd === 'initiative_update')).toBe(false);
    expect(onClose).not.toHaveBeenCalled();
  });
  it('renders persisted decision links, retains failed unlink, and projects the acknowledged record', async () => {
    const { calls, fail, store, onClose } = setup('decision');
    await waitFor(() => expect(screen.getByLabelText('Rationale')).toHaveValue('Full rationale'));
    expect(screen.getByText('Task · Linked task')).toBeVisible(); expect(screen.getByText('Engineer · Engineer')).toBeVisible();
    expect(screen.getByLabelText('Status')).toHaveValue('revised'); expect(screen.queryByRole('option', { name: 'superseded' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Keep rationale' } });
    fail('architect_decision_update'); fireEvent.click(screen.getByRole('button', { name: 'Unlink task t' }));
    await screen.findByText('Write rejected'); expect(screen.getByRole('button', { name: 'Unlink task t' })).toBeVisible();
    fail(''); fireEvent.click(screen.getByRole('button', { name: 'Unlink task t' })); await waitFor(() => expect(screen.queryByRole('button', { name: 'Unlink task t' })).not.toBeInTheDocument());
    expect(screen.getByLabelText('Rationale')).toHaveValue('Keep rationale');
    expect((selectPlanningState(store.getState()).decisions.d as UnknownRecord).linked_task_ids).toEqual([]);
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'accepted' } });
    fireEvent.click(screen.getByRole('button', { name: 'Archive' })); await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(calls.at(-1)).toMatchObject({ cmd: 'architect_decision_update', id: 'd', architect_id: 'a', archived: true, status: 'accepted', rationale: 'Keep rationale' });
    expect((selectPlanningState(store.getState()).decisions.d as UnknownRecord).archived).toBe(true);
  });
  it('does not replay an acknowledged Initiative edit when archive fails and an external edit arrives', async () => {
    const { calls, store, fail, onClose } = setup('initiative');
    await waitFor(() => expect(screen.getByLabelText('Summary')).toHaveValue('Full summary'));
    fireEvent.change(screen.getByLabelText('Why this matters'), { target: { value: 'Acknowledged edit' } });
    fail('initiative_archive'); fireEvent.click(screen.getByRole('button', { name: 'Archive' }));
    await screen.findByText('Write rejected'); expect(screen.getByLabelText('Why this matters')).toHaveValue('Acknowledged edit');
    act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'initiative_updated', initiative: { ...initiative, why: 'External edit after save' } })); });
    expect(screen.getByLabelText('Why this matters')).toHaveValue('External edit after save');
    fail(''); fireEvent.click(screen.getByRole('button', { name: 'Archive' })); await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(calls.filter((command) => command.cmd === 'initiative_update')).toEqual([{ cmd: 'initiative_update', id: 'i', why: 'Acknowledged edit' }]);
    expect(calls.filter((command) => command.cmd === 'initiative_archive')).toHaveLength(2);
  });
  it('scopes decisions and hides archived records until requested', () => {
    const store = createAppStore();
    store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, agents: { a: { id: 'a', kind: 'architect', group: 'Foundation' }, b: { id: 'b', kind: 'architect', group: 'Other' } }, pending_hires: { local: { id: 'local', architect_id: 'a', requested_name: 'Local hire' }, remote: { id: 'remote', architect_id: 'b', requested_name: 'Other hire' } }, engineer_journal: { a: [{ id: 'j-local', entry: 'Local journal', type: 'checkpoint', timestamp: 1710000000 }], b: [{ id: 'j-remote', entry: 'Other journal', type: 'checkpoint', timestamp: 1710000000 }] }, decisions: { d: decision, old: { ...decision, id: 'old', title: 'Archived decision', archived: true }, other: { ...decision, id: 'other', title: 'Other group decision', architect_id: 'b' } } }));
    render(<Provider store={store}><PlanningWorkspace group="Foundation" sendCommand={() => true} onCommandUnavailable={vi.fn()} /></Provider>);
    fireEvent.click(screen.getByRole('button', { name: 'Decisions' }));
    const region = screen.getByRole('region', { name: 'Planning' });
    expect(within(region).queryByRole('button', { name: /Other group decision/ })).not.toBeInTheDocument();
    expect(within(region).queryByRole('button', { name: /Archived decision/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Show archived decisions'));
    expect(within(region).getByRole('button', { name: /Archived decision/ })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Hires & journals' }));
    expect(screen.getByRole('button', { name: /Local hire/ })).toBeVisible();
    expect(screen.queryByRole('button', { name: /Other hire/ })).not.toBeInTheDocument();
    expect(screen.getByText('Local journal', { selector: 'summary strong' })).toBeVisible();
    expect(screen.queryByText('Other journal')).not.toBeInTheDocument();
  });
});

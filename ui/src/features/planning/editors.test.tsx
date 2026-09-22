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
  let record: UnknownRecord = structuredClone(kind === 'initiative' ? initiative : decision); let fail = '';
  store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, initiatives: { i: { id: 'i', title: 'Roadmap' } }, decisions: { d: { id: 'd', title: 'Decision', architect_id: 'a' } } }));
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
    const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; calls.push(command);
    if (command.cmd === fail) return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: false, error: 'Write rejected' }) });
    let data: UnknownRecord;
    if (command.cmd === 'initiative_show') data = { ...record, type: 'initiative' };
    else if (command.cmd === 'decisions_snapshot') data = { type: 'decisions_snapshot', decisions: { d: record } };
    else if (command.cmd === 'initiative_link_task' || command.cmd === 'initiative_unlink_task') {
      record = { ...record, links: { tasks: command.cmd === 'initiative_link_task' ? ['t'] : [], decisions: [] } };
      data = { type: 'initiative_task_linked' };
    } else {
      record = { ...record, ...command, ...(command.cmd === 'initiative_archive' ? { archived: true } : {}) };
      data = kind === 'initiative' ? { type: 'initiative_updated', initiative: record } : { ...record, type: 'ok' };
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
  it('scopes decisions and hides archived records until requested', () => {
    const store = createAppStore();
    store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, agents: { a: { id: 'a', kind: 'architect', group: 'Foundation' }, b: { id: 'b', kind: 'architect', group: 'Other' } }, pending_hires: { local: { id: 'local', architect_id: 'a', requested_name: 'Local hire' }, remote: { id: 'remote', architect_id: 'b', requested_name: 'Other hire' } }, engineer_journal: { a: [{ id: 'j-local', title: 'Local journal' }], b: [{ id: 'j-remote', title: 'Other journal' }] }, decisions: { d: decision, old: { ...decision, id: 'old', title: 'Archived decision', archived: true }, other: { ...decision, id: 'other', title: 'Other group decision', architect_id: 'b' } } }));
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
    expect(screen.getByRole('button', { name: /Local journal/ })).toBeVisible();
    expect(screen.queryByRole('button', { name: /Other journal/ })).not.toBeInTheDocument();
  });
});

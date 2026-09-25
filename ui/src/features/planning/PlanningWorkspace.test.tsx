import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { readCommand } from '../../protocol/http';
import type { AuxiliaryFrame, TorqueCommand } from '../../protocol';
import { PlanningWorkspace } from './PlanningWorkspace';
import { planningReads, type PlanningTab } from './planningReads';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const initiative = { id: 'initiative-1', group: 'Foundation', title: 'Retained initiative', summary: 'Saved summary', planning_status: 'triage' };
function response(command: TorqueCommand): AuxiliaryFrame {
  if (command.cmd === 'initiative_list') return { type: 'initiative_list', group: command.group, initiatives: [initiative] };
  if (command.cmd === 'initiative_show') return { type: 'initiative', ...initiative };
  if (command.cmd === 'area_list') return { type: 'area_list', group: command.group, areas: [{ id: 'area-1', group: 'Foundation', title: 'Planning area' }] };
  if (command.cmd === 'area_show') return { type: 'area', id: 'area-1', group: 'Foundation', title: 'Planning area' };
  if (command.cmd === 'scratchpad_note_list') return { type: 'scratchpad_note_list', group: command.group, notes: [] };
  if (command.cmd === 'idea_brief_list') return { type: 'idea_brief_list', group: command.group, idea_briefs: [] };
  if (command.cmd === 'decisions_snapshot') return { type: 'decisions_snapshot', decisions: { decision: { id: 'decision', group: 'Foundation', title: 'Link choice' } } };
  return { type: command.cmd, engineer_journal: {}, pending_hires: {} };
}
beforeEach(() => { read.mockReset(); read.mockImplementation((command) => Promise.resolve(response(command))); });
function mount() {
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture));
  const view = render(<Provider store={store}><PlanningWorkspace group="Foundation" sendCommand={() => true} onCommandUnavailable={() => {}} /></Provider>);
  const reconnect = () => act(() => { store.dispatch(connectionActions.disconnected({ at: 2 })); store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); });
  return { store, reconnect, ...view };
}
describe('Planning visible-section reads', () => {
  it('maps all sections and editor dependencies without unrelated collections', () => {
    const expected: Record<PlanningTab, string[]> = { roadmap: ['initiative_list'], areas: ['area_list'], thinking: ['scratchpad_note_list', 'idea_brief_list'], decisions: ['decisions_snapshot'], team: ['pending_hires_snapshot', 'engineer_journal_snapshot'], schedules: [] };
    for (const [tab, commands] of Object.entries(expected)) expect(planningReads(tab as PlanningTab, 'G', false, false, undefined).map((read) => read.command.cmd)).toEqual(commands);
    expect(planningReads('areas', 'G', false, false, 'area').map((read) => read.command.cmd)).toEqual(['area_list', 'decisions_snapshot', 'initiative_list']);
    expect(planningReads('roadmap', 'G', false, false, 'initiative').map((read) => read.command.cmd)).toEqual(['initiative_list', 'decisions_snapshot']);
    expect(planningReads('thinking', 'G', true, false, undefined).every((read) => read.command.include_archived === true)).toBe(true);
  });
  it('retains cards and an editor draft through compact reconnect, rejected group and retry; normal live deltas still apply', async () => {
    const app = mount(); await screen.findByText('Retained initiative'); expect(read.mock.calls.map(([cmd]) => cmd.cmd)).toEqual(['initiative_list']);
    fireEvent.click(screen.getByRole('button', { name: /Retained initiative/ })); const dialog = await screen.findByRole('dialog', { name: 'Initiative' });
    await waitFor(() => expect(within(dialog).queryByText('Loading full details…')).not.toBeInTheDocument());
    fireEvent.change(within(dialog).getByLabelText('Link type'), { target: { value: 'decision' } }); expect(await within(dialog).findByRole('option', { name: 'Link choice' })).toBeInTheDocument();
    const input = within(dialog).getByLabelText('Title'); fireEvent.change(input, { target: { value: 'Unfinished initiative' } }); act(() => { input.focus(); (input as HTMLInputElement).setSelectionRange(2, 7); });
    const card = screen.getByText('Retained initiative');
    read.mockImplementation((command) => command.cmd === 'initiative_list' ? Promise.resolve({ type: 'initiative_list', group: 'wrong', initiatives: [] }) : Promise.resolve(response(command)));
    app.reconnect(); await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('requested section or group'));
    expect(screen.getByText('Retained initiative')).toBe(card); expect(input).toHaveFocus(); expect(input).toHaveValue('Unfinished initiative'); expect((input as HTMLInputElement).selectionStart).toBe(2);
    read.mockImplementation((command) => Promise.resolve(response(command))); fireEvent.click(screen.getByRole('button', { name: 'Retry Planning' })); await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument()); expect(input).toHaveValue('Unfinished initiative');
    act(() => { app.store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'initiative', ...initiative, title: 'Live title' })); }); expect(screen.getByText('Live title')).toBeVisible(); expect(input).toHaveValue('Unfinished initiative');
  });
  it('cancels obsolete sections, ignores late results, scopes archive reads, and makes no auxiliary reads for Schedules or after unmount', async () => {
    let release!: (frame: AuxiliaryFrame) => void; read.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    const app = mount(); const signal = read.mock.calls[0]![1]; fireEvent.click(screen.getByRole('button', { name: 'Thinking' })); await waitFor(() => expect(read).toHaveBeenCalledTimes(3)); expect(signal.aborted).toBe(true);
    await act(async () => { release({ type: 'initiative_list', group: 'Foundation', initiatives: [initiative] }); await Promise.resolve(); }); expect(app.store.getState().projection.data.initiatives).toBeUndefined();
    fireEvent.click(screen.getByLabelText('Show archived Thinking')); await waitFor(() => expect(read).toHaveBeenCalledTimes(5)); expect(read.mock.calls.slice(-2).map(([command]) => command)).toEqual([{ cmd: 'scratchpad_note_list', group: 'Foundation', include_archived: true }, { cmd: 'idea_brief_list', group: 'Foundation', include_archived: true }]);
    fireEvent.click(screen.getByRole('button', { name: 'Schedules' })); app.reconnect(); expect(read).toHaveBeenCalledTimes(5);
    app.unmount(); app.reconnect(); expect(read).toHaveBeenCalledTimes(5);
  });
  it('times out stalled reads and retries in place', async () => {
    vi.useFakeTimers();
    try { read.mockImplementation(() => new Promise(() => {})); mount(); const signal = read.mock.calls[0]![1]; await act(async () => { await vi.advanceTimersByTimeAsync(30_000); }); expect(signal.aborted).toBe(true); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(screen.getByRole('button', { name: 'Retry Planning' })).toBeEnabled(); } finally { vi.useRealTimers(); }
  });
});

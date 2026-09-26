import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createAppStore, projectionActions, selectCatalogState, selectPlanningState } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { AuxiliaryFrame, TorqueCommand } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { AreaEditor } from './AreaEditor';
import { DecisionEditor, InitiativeEditor } from './PlanningEditors';
import { ThinkingEditor } from './ThinkingEditor';
import { InitiativeTaskCreator } from './InitiativeTaskCreator';
import { PlanningWorkspace } from './PlanningWorkspace';

vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const item = { id: 'record', title: 'Saved title', group_name: 'Foundation', architect_id: 'architect', problem_opportunity: 'Saved problem', rationale: 'Saved rationale', links: {}, notes: [] };
const targets = { task: [], decision: [], initiative: [], area: [] };
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
function deferred() {
  let resolve!: (frame: AuxiliaryFrame) => void;
  const promise = new Promise<AuxiliaryFrame>((done) => { resolve = done; });
  return { promise, resolve };
}
function mount(children: React.ReactNode) {
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
  return { ...render(<Provider store={store}>{children}</Provider>), store };
}
function detail(kind: string): AuxiliaryFrame {
  return kind === 'decision' ? { type: 'decisions_snapshot', decisions: { record: item } }
    : { ...item, type: kind === 'note' ? 'scratchpad_note' : kind === 'brief' ? 'idea_brief' : kind };
}
function editor(kind: string, close = vi.fn()) {
  if (kind === 'area') return <AreaEditor item={item} targets={targets} onClose={close} />;
  if (kind === 'initiative') return <InitiativeEditor item={item} tasks={[]} decisions={[]} onClose={close} />;
  if (kind === 'decision') return <DecisionEditor item={item} tasks={[]} engineers={[]} decisions={[]} onClose={close} />;
  return <ThinkingEditor kind={kind === 'brief' ? 'brief' : 'note'} item={item} notes={[]} onClose={close} />;
}
beforeEach(() => { read.mockReset(); vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('Planning request recovery', () => {
  it('accepts the real compact Decision creation acknowledgement and rejects an incomplete one without losing its draft', async () => {
    read.mockResolvedValueOnce({ type: 'ok', id: 'decision-new' });
    read.mockResolvedValue({ type: 'ok', id: 'decision-new', created_at: 1790401165 });
    const store = createAppStore();
    store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, agents: { architect: { id: 'architect', kind: 'architect', group: 'Foundation', name: 'Architect' } } }));
    render(<Provider store={store}><PlanningWorkspace group="Foundation" sendCommand={() => true} onCommandUnavailable={vi.fn()} /></Provider>);
    fireEvent.click(screen.getByRole('button', { name: 'Decisions' })); fireEvent.click(screen.getByRole('button', { name: '＋ New' }));
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Created decision' } });
    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Reviewed rationale' } });
    fireEvent.change(screen.getByLabelText('Architect'), { target: { value: 'architect' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' })); await flush();
    expect(screen.getByRole('alert')).toHaveTextContent('invalid acknowledgement');
    expect(screen.getByLabelText('Rationale')).toHaveValue('Reviewed rationale');
    fireEvent.click(screen.getByRole('button', { name: 'Create' })); await flush();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(selectPlanningState(store.getState()).decisions['decision-new']).toMatchObject({ id: 'decision-new', architect_id: 'architect', title: 'Created decision', rationale: 'Reviewed rationale', created_at: 1790401165 });
  });

  it.each(['note', 'brief', 'initiative', 'decision', 'area'])('bounds %s detail loading and ignores the late response after retry', async (kind) => {
    const held = deferred(); let signal: AbortSignal | undefined;
    read.mockImplementationOnce((_command, owner) => { signal = owner; return held.promise; });
    read.mockResolvedValue(detail(kind));
    mount(editor(kind));
    const title = screen.getByRole<HTMLInputElement>('textbox', { name: 'Title' });
    fireEvent.change(title, { target: { value: 'Keep early draft' } }); title.focus(); title.setSelectionRange(2, 6);
    await advance(15_001);
    expect(screen.getByRole('alert')).toHaveTextContent(/timed out/i);
    expect(signal?.aborted).toBe(true); expect(title).toHaveValue('Keep early draft');
    expect(title).toHaveFocus(); expect([title.selectionStart, title.selectionEnd]).toEqual([2, 6]);
    fireEvent.click(screen.getByRole('button', { name: kind === 'area' ? 'Retry Area details' : 'Retry details' })); await flush();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await act(async () => { held.resolve({ ...detail(kind), title: 'Obsolete reply' }); await Promise.resolve(); });
    expect(title).toHaveValue('Keep early draft');
  });

  it('stops a dependent lifecycle write when its editor unmounts before save acknowledgement', async () => {
    const held = deferred(); let signal: AbortSignal | undefined;
    read.mockImplementation((command, owner) => {
      if (command.cmd === 'idea_brief_show') return Promise.resolve(detail('brief'));
      if (command.cmd === 'idea_brief_update') { signal = owner; return held.promise; }
      return Promise.resolve({ type: 'idea_brief_parked', idea_brief: { ...item, status: 'parked' } });
    });
    const close = vi.fn(); const view = mount(editor('brief', close)); await flush();
    fireEvent.change(screen.getByLabelText('Why it matters'), { target: { value: 'Reviewed scope' } });
    fireEvent.click(screen.getByRole('button', { name: 'Park' })); await flush();
    view.unmount(); expect(signal?.aborted).toBe(true);
    await act(async () => { held.resolve({ type: 'idea_brief_updated', idea_brief: { ...item, why_it_matters: 'Reviewed scope' } }); await Promise.resolve(); });
    expect(read.mock.calls.map(([command]) => command.cmd)).toEqual(['idea_brief_show', 'idea_brief_update']);
    expect(close).not.toHaveBeenCalled();
  });

  it('retains an acknowledged intermediate save when a subsequent lifecycle observation times out', async () => {
    const held = deferred(); let parkCount = 0;
    read.mockImplementation((command) => {
      if (command.cmd === 'idea_brief_show') return Promise.resolve(detail('brief'));
      if (command.cmd === 'idea_brief_update') return Promise.resolve({ type: 'idea_brief_updated', idea_brief: { ...item, why_it_matters: 'Already saved' } });
      if (++parkCount === 1) return held.promise;
      return Promise.resolve({ type: 'idea_brief_parked', idea_brief: { ...item, status: 'parked' } });
    });
    mount(editor('brief')); await flush();
    fireEvent.change(screen.getByLabelText('Why it matters'), { target: { value: 'Already saved' } });
    fireEvent.click(screen.getByRole('button', { name: 'Park' })); await flush(); await advance(30_001);
    expect(screen.getByRole('alert')).toHaveTextContent(/outcome is unknown/i);
    expect(screen.getByLabelText('Why it matters')).toHaveValue('Already saved');
    expect(screen.getByRole('button', { name: 'Park' })).toBeEnabled();
    await act(async () => { held.resolve({ type: 'idea_brief_parked', idea_brief: { ...item, title: 'Late title', status: 'parked' } }); await Promise.resolve(); });
    expect(screen.getByLabelText('Title')).toHaveValue('Saved title');
    fireEvent.click(screen.getByRole('button', { name: 'Park' })); await flush();
    expect(read.mock.calls.filter(([command]) => command.cmd === 'idea_brief_update')).toHaveLength(1);
    expect(read.mock.calls.filter(([command]) => command.cmd === 'idea_brief_park')).toHaveLength(2);
  });

  it.each([
    { type: 'unrelated_updated', note: { id: 17, area_id: 'record' } },
    { type: 'area_note_created', note: { id: 17, area_id: 'another-area' } },
  ])('retains an Area note draft after an invalid acknowledgement: $type', async (frame) => {
    read.mockImplementation((command) => Promise.resolve(command.cmd === 'area_show' ? detail('area') : frame));
    mount(editor('area')); await flush();
    fireEvent.change(screen.getByLabelText('Note title'), { target: { value: 'Keep note' } });
    fireEvent.change(screen.getByLabelText('Note body'), { target: { value: 'Keep body' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add note' })); await flush();
    expect(screen.getByRole('alert')).toHaveTextContent(/acknowledgement|response/i);
    expect(screen.getByLabelText('Note title')).toHaveValue('Keep note');
    expect(screen.getByLabelText('Note body')).toHaveValue('Keep body');
  });

  it('bounds task-option discovery while retaining the reviewed task draft', async () => {
    const held = deferred();
    read.mockImplementation((command) => command.cmd === 'list_roles' ? held.promise : Promise.resolve({ type: 'actions', actions: [] }));
    mount(<InitiativeTaskCreator initiative={item} disabled={false} onLinked={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Create Board task' })); await flush();
    const dialog = within(screen.getByRole('dialog', { name: 'Create Board task' }));
    fireEvent.change(dialog.getByLabelText('Title'), { target: { value: 'Reviewed title' } }); await advance(15_001);
    expect(dialog.getByRole('alert')).toHaveTextContent(/timed out/i);
    expect(dialog.getByLabelText('Title')).toHaveValue('Reviewed title');
    read.mockImplementation((command) => Promise.resolve(command.cmd === 'list_roles' ? { type: 'roles', roles: [] } : { type: 'actions', actions: [] }));
    fireEvent.click(dialog.getByRole('button', { name: 'Retry task options' })); await flush();
    expect(dialog.queryByRole('alert')).not.toBeInTheDocument();
    expect(dialog.getByLabelText('Title')).toHaveValue('Reviewed title');
  });

  it('rejects catalog responses for another group without replacing accepted options', async () => {
    read.mockImplementation((command) => Promise.resolve(command.cmd === 'list_roles'
      ? { type: 'roles', group: 'Other', roles: [{ slug: 'wrong-role' }] }
      : { type: 'actions', actions: [] }));
    const { store } = mount(<InitiativeTaskCreator initiative={item} disabled={false} onLinked={vi.fn()} />);
    const before = selectCatalogState(store.getState()).roles;
    fireEvent.click(screen.getByRole('button', { name: 'Create Board task' })); await flush();
    expect(screen.getByRole('alert')).toHaveTextContent('requested catalog or group');
    expect(selectCatalogState(store.getState()).roles).toEqual(before);
  });

  it('cancels hidden task-option reads and ignores a late catalog after closing the dialog', async () => {
    const held = deferred(); let signal: AbortSignal | undefined;
    read.mockImplementation((command, owner) => {
      if (command.cmd === 'list_roles') { signal = owner; return held.promise; }
      return Promise.resolve({ type: 'actions', actions: [] });
    });
    const { store } = mount(<InitiativeTaskCreator initiative={item} disabled={false} onLinked={vi.fn()} />);
    const before = selectCatalogState(store.getState()).roles;
    fireEvent.click(screen.getByRole('button', { name: 'Create Board task' })); await flush();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' })); await flush();
    expect(signal?.aborted).toBe(true);
    await act(async () => { held.resolve({ type: 'roles', roles: [{ slug: 'obsolete-role' }] }); await Promise.resolve(); });
    expect(selectCatalogState(store.getState()).roles).toEqual(before);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('retries a timed-out Initiative task link using the known task ID without recreating it', async () => {
    const held = deferred(); let linkCount = 0; const linked = vi.fn(); const calls: TorqueCommand[] = [];
    read.mockImplementation((command) => {
      calls.push(command);
      if (command.cmd === 'list_actions') return Promise.resolve({ type: 'actions', actions: [] });
      if (command.cmd === 'list_roles') return Promise.resolve({ type: 'roles', roles: [] });
      if (command.cmd === 'board_add_task') return Promise.resolve({ type: 'board_task_added', task_id: 'created-task' });
      if (++linkCount === 1) return held.promise;
      if (linkCount === 2) return Promise.resolve({ type: 'initiative_task_linked', link: { initiative_id: 'record', link_type: 'task', target_id: 'wrong-task' } });
      return Promise.resolve({ type: 'initiative_task_linked', link: { initiative_id: 'record', link_type: 'task', target_id: 'created-task' } });
    });
    mount(<InitiativeTaskCreator initiative={item} disabled={false} onLinked={linked} />);
    fireEvent.click(screen.getByRole('button', { name: 'Create Board task' })); await flush();
    const dialog = within(screen.getByRole('dialog', { name: 'Create Board task' }));
    fireEvent.click(dialog.getByRole('button', { name: 'Create task' })); await flush(); await advance(30_001);
    expect(dialog.getByRole('alert')).toHaveTextContent(/outcome is unknown/i);
    expect(dialog.getByRole('button', { name: 'Retry link' })).toBeEnabled(); expect(linked).not.toHaveBeenCalled();
    await act(async () => { held.resolve({ type: 'initiative_task_linked', link: { initiative_id: 'record', link_type: 'task', target_id: 'created-task' } }); await Promise.resolve(); });
    expect(linked).not.toHaveBeenCalled();
    fireEvent.click(dialog.getByRole('button', { name: 'Retry link' })); await flush();
    expect(dialog.getByRole('alert')).toHaveTextContent('invalid acknowledgement'); expect(linked).not.toHaveBeenCalled();
    fireEvent.click(dialog.getByRole('button', { name: 'Retry link' })); await flush();
    expect(linked).toHaveBeenCalledOnce();
    expect(calls.filter((command) => command.cmd === 'board_add_task')).toHaveLength(1);
    expect(calls.filter((command) => command.cmd === 'initiative_link_task')).toEqual([
      { cmd: 'initiative_link_task', id: 'record', task_id: 'created-task' },
      { cmd: 'initiative_link_task', id: 'record', task_id: 'created-task' },
      { cmd: 'initiative_link_task', id: 'record', task_id: 'created-task' },
    ]);
  });
});

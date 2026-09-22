import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions, selectPlanningState } from '../../app/store';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { compactStateFixture } from '../../protocol/fixtures';
import { PlanningWorkspace } from './PlanningWorkspace';
import { ThinkingEditor } from './ThinkingEditor';

const brief = { id: 'brief-1', group: 'Foundation', title: 'Brief', status: 'draft', problem_opportunity: 'Real problem', why_it_matters: 'Real value', thinking_links: [], source_context: { source: 'keep this' } };
afterEach(() => vi.unstubAllGlobals());
function setup(kind: 'note' | 'brief' = 'brief') {
  const store = createAppStore(); const calls: TorqueCommand[] = []; const onClose = vi.fn();
  let detail: UnknownRecord = kind === 'brief' ? brief : { id: 'brief-1', title: 'Note', body: 'Body', context: { source: 'preserve' }, links: [{ id: 'existing' }] };
  let fail = false;
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
    const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; calls.push(command);
    const showing = String(command.cmd).endsWith('_show');
    if (!showing && fail) return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: false, error: 'Write rejected' }) });
    if (!showing) {
      detail = { ...detail, ...command, ...(command.cmd === 'idea_brief_propose' ? { status: 'proposed' } : {}) };
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: showing ? { ...detail, type: kind === 'brief' ? 'idea_brief' : 'scratchpad_note' } : { type: `${kind === 'brief' ? 'idea_brief' : 'scratchpad_note'}_updated`, [kind === 'brief' ? 'idea_brief' : 'note']: detail } }) });
  }));
  render(<Provider store={store}><ThinkingEditor kind={kind} item={{ id: 'brief-1', title: kind === 'brief' ? 'Brief' : 'Note' }} notes={[{ id: 'note-1', title: 'Evidence', body: 'Source text', group: 'Foundation' }]} onClose={onClose} /></Provider>);
  return { store, calls, onClose, fail: (value: boolean) => { fail = value; }, detail: () => detail };
}
describe('Thinking contracts and acknowledged lifecycle', () => {
  it('hydrates full brief fields, preserves edits/caret on reconnect and sends only edits', async () => {
    const { store, calls, onClose, fail } = setup();
    await waitFor(() => expect(screen.getByLabelText('Problem or opportunity')).toHaveValue('Real problem'));
    const field = screen.getByLabelText<HTMLTextAreaElement>('Why it matters');
    fireEvent.change(field, { target: { value: 'Keep my draft' } }); field.focus(); field.setSelectionRange(2, 5);
    act(() => { store.dispatch(connectionActions.connected({ at: 2000, reconnect: true })); });
    await waitFor(() => expect(calls.filter((call) => call.cmd === 'idea_brief_show')).toHaveLength(2));
    expect(field).toHaveValue('Keep my draft'); expect(field).toHaveFocus(); expect(field.selectionStart).toBe(2);
    fail(true); fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Write rejected'); expect(onClose).not.toHaveBeenCalled(); expect(field).toHaveValue('Keep my draft');
    fail(false); fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(calls.find((call) => call.cmd === 'idea_brief_update')).toEqual({ cmd: 'idea_brief_update', id: 'brief-1', why_it_matters: 'Keep my draft' });
  });
  it('adds typed links and saves changes before proposal; failed save prevents lifecycle mutation', async () => {
    const { calls, fail, detail } = setup();
    await waitFor(() => expect(screen.getByLabelText('Problem or opportunity')).toHaveValue('Real problem'));
    fireEvent.change(screen.getByLabelText('Scratchpad note'), { target: { value: 'note-1' } });
    fireEvent.change(screen.getByLabelText('Why this link matters'), { target: { value: 'Evidence context' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add link' }));
    expect(screen.getByText('Source text')).not.toBeVisible();
    fireEvent.click(screen.getByText('Read linked note')); expect(screen.getByText('Source text')).toBeVisible();
    fail(true); fireEvent.click(screen.getByRole('button', { name: 'Propose for review' }));
    await screen.findByRole('alert'); expect(calls.some((call) => call.cmd === 'idea_brief_propose')).toBe(false);
    fail(false); fireEvent.click(screen.getByRole('button', { name: 'Propose for review' }));
    await screen.findByText('Status: proposed');
    expect(detail().thinking_links).toMatchObject([{ type: 'scratchpad_note', id: 'note-1', context: 'Evidence context' }]);
    const writes = calls.filter((call) => call.cmd !== 'idea_brief_show');
    expect(writes.map((call) => call.cmd)).toEqual(['idea_brief_update', 'idea_brief_update', 'idea_brief_propose']);
    expect(detail().source_context).toEqual({ source: 'keep this' });
  });
  it('retains note drafts after a failed write and preserves hidden metadata in a sparse save', async () => {
    const { calls, onClose, fail } = setup('note');
    await waitFor(() => expect(screen.getByLabelText('Body')).toHaveValue('Body'));
    fireEvent.change(screen.getByLabelText('Body'), { target: { value: 'New body' } });
    fail(true); fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByRole('alert'); expect(onClose).not.toHaveBeenCalled();
    fail(false); fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(calls.find((call) => call.cmd === 'scratchpad_note_update')).toEqual({ cmd: 'scratchpad_note_update', id: 'brief-1', body: 'New body' });
  });
  it('gates writes on full detail and lets a failed hydration retry without losing early edits', async () => {
    const store = createAppStore(); let failing = true; const calls: TorqueCommand[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; calls.push(command);
      return Promise.resolve({ ok: true, json: () => Promise.resolve(failing ? { ok: false, error: 'Detail unavailable' } : { ok: true, data: { ...brief, type: 'idea_brief' } }) });
    }));
    render(<Provider store={store}><ThinkingEditor kind="brief" item={{ id: 'brief-1', title: 'Summary' }} notes={[]} onClose={vi.fn()} /></Provider>);
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Early edit' } });
    await screen.findByText('Detail unavailable');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Propose for review' })).toBeDisabled();
    failing = false; fireEvent.click(screen.getByRole('button', { name: 'Retry details' }));
    await waitFor(() => expect(screen.getByLabelText('Problem or opportunity')).toHaveValue('Real problem'));
    expect(screen.getByLabelText('Title')).toHaveValue('Early edit');
    expect(calls.every((command) => command.cmd === 'idea_brief_show')).toBe(true);
  });

  it('requires a valid brief problem and retains the creation form after failure', async () => {
    const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
    const calls: TorqueCommand[] = []; let fail = true;
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; calls.push(command);
      return Promise.resolve({ ok: true, json: () => Promise.resolve(fail ? { ok: false, error: 'Creation rejected' } : { ok: true, data: { type: 'idea_brief_created', idea_brief: { ...brief, ...command } } }) });
    }));
    render(<Provider store={store}><PlanningWorkspace group="Foundation" sendCommand={() => true} onCommandUnavailable={vi.fn()} /></Provider>);
    fireEvent.click(screen.getByRole('button', { name: 'Thinking' })); fireEvent.click(screen.getByRole('button', { name: '＋ Brief' }));
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'New brief' } });
    expect(screen.getByRole('button', { name: 'Create' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Problem or opportunity'), { target: { value: 'Real need' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' })); await screen.findByText('Creation rejected');
    expect(screen.getByLabelText('Problem or opportunity')).toHaveValue('Real need');
    fail = false; fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(calls[0]).toMatchObject({ cmd: 'idea_brief_create', title: 'New brief', problem_opportunity: 'Real need' });
  });
});


it.each(['note', 'brief'] as const)('retains archived %s detail after a late mutation acknowledgement', (kind) => {
  const store = createAppStore();
  store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
  const archived = { id: 'archived', group: 'Foundation', title: 'Keep archive', archived: true, status: 'archived' };
  const receive = (frame: { type: string; [key: string]: unknown }) => store.dispatch(projectionActions.auxiliaryResourceReceived(frame));
  receive(kind === 'note' ? { type: 'scratchpad_note_list', notes: [archived] } : { type: 'idea_brief_list', idea_briefs: [archived] });
  receive(kind === 'note' ? { type: 'scratchpad_note_archived', note: archived } : { type: 'idea_brief_archived', idea_brief: archived });
  receive({ type: kind === 'note' ? 'scratchpad_note' : 'idea_brief', ...archived });
  const planning = selectPlanningState(store.getState());
  expect((kind === 'note' ? planning.scratchpadNotes : planning.ideaBriefs).archived).toMatchObject(archived);
  if (kind === 'note') {
    receive({ type: 'scratchpad_note_deleted', note: { ...archived, deleted: true } });
    expect(selectPlanningState(store.getState()).scratchpadNotes.archived).toBeUndefined();
  }
});

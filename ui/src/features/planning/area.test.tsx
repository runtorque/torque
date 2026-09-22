import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useAppSelector } from '../../app/hooks';
import { connectionActions, createAppStore, projectionActions, selectPlanningState } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { AreaEditor } from './AreaEditor';
import { areaLinks } from './areaModel';
const area = { id: 'a', title: 'Area A', group: 'Foundation', lifecycle: 'planned', links: { tasks: ['task'], decisions: [], initiatives: ['initiative'], areas: [{ area_id: 'b', relation: 'depends_on' }] }, notes: [] };
const targets = { task: [{ id: 'task', task: 'Task target' }], decision: [], initiative: [{ id: 'initiative', title: 'Initiative target' }], area: [{ id: 'b', title: 'Area B' }] };
function Harness() { const planning = useAppSelector(selectPlanningState); return <AreaEditor item={planning.areas.a as UnknownRecord} targets={targets} onClose={vi.fn()} />; }
afterEach(() => vi.unstubAllGlobals());
describe('Area parity', () => {
  it('normalizes every relationship and preserves composite Area relations', () => { expect(areaLinks(area.links)).toEqual([{ link_type: 'task', target_id: 'task', relation: '' }, { link_type: 'initiative', target_id: 'initiative', relation: '' }, { link_type: 'area', target_id: 'b', relation: 'depends_on' }]); });
  it('hydrates compact notes, saves valid note types, retains failed edits, and restores relationships after reconnect', async () => {
    const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, planning_areas: { a: area } }));
    let notes: UnknownRecord[] = []; let fail = false; const calls: TorqueCommand[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; calls.push(command);
      if (fail) return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: false, error: 'Note rejected' }) });
      if (command.cmd === 'area_note_create') notes = [{ ...command, id: 17, area_id: 'a' }];
      if (command.cmd === 'area_note_update') notes = [{ ...command, id: 17, area_id: 'a' }];
      if (command.cmd === 'area_note_archive') notes = [];
      const frame = command.cmd === 'area_show' ? { type: 'area', ...area, notes } : { type: 'area_note_created', note: notes[0] };
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: frame }) });
    }));
    render(<Provider store={store}><Harness /></Provider>);
    expect(screen.getByRole('button', { name: 'Unlink initiative initiative' })).toBeVisible(); expect(screen.getByText(/Area B · depends_on/)).toBeVisible();
    fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), { target: { value: 'Keep local title' } });
    fireEvent.change(screen.getByLabelText('Lifecycle'), { target: { value: 'stable' } });
    fireEvent.change(screen.getByLabelText('Note title'), { target: { value: 'Caveat' } }); fireEvent.change(screen.getByLabelText('Note body'), { target: { value: 'Durable warning' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add note' }));
    expect(await screen.findByRole('button', { name: 'Edit note Caveat' })).toBeVisible();
    expect(calls.find((command) => command.cmd === 'area_note_create')).toMatchObject({ note_type: 'caveat', body: 'Durable warning' });
    fireEvent.click(screen.getByRole('button', { name: 'Edit note Caveat' })); fireEvent.change(screen.getByLabelText('Note body'), { target: { value: 'Changed warning' } });
    fail = true; fireEvent.click(screen.getByRole('button', { name: 'Save note' })); expect(await screen.findByRole('alert')).toHaveTextContent('Note rejected'); expect(screen.getByLabelText('Note body')).toHaveValue('Changed warning');
    fail = false; fireEvent.click(screen.getByRole('button', { name: 'Save note' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit note Caveat' }).closest('article')).toHaveTextContent('Changed warning'));
    expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Keep local title');
    const readsBeforeReconnect = calls.filter((command) => command.cmd === 'area_show').length;
    notes = notes.map((entry) => ({ ...entry, body: 'Fresh after reconnect' }));
    act(() => { store.dispatch(connectionActions.connected({ at: 2000, reconnect: true })); });
    await waitFor(() => expect(calls.filter((command) => command.cmd === 'area_show').length).toBeGreaterThan(readsBeforeReconnect));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit note Caveat' }).closest('article')).toHaveTextContent('Fresh after reconnect'));
    expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Keep local title');
    fireEvent.click(screen.getByRole('button', { name: 'Archive note Caveat' })); await waitFor(() => expect(screen.queryByRole('button', { name: 'Edit note Caveat' })).not.toBeInTheDocument());
  });
  it('projects relationship removals without IDs and note updates into the open Area', () => {
    const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, planning_areas: { a: area } }));
    store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'area_link_remove', area_id: 'a', link_type: 'area', target_id: 'b', relation: 'depends_on' }, { op: 'area_note_upsert', id: 17, area_id: 'a', title: 'Added', body: 'Note' }] }));
    expect((selectPlanningState(store.getState()).areas.a as UnknownRecord).links).toEqual({ ...area.links, areas: [] });
    expect((selectPlanningState(store.getState()).areas.a as UnknownRecord).notes).toMatchObject([{ id: 17, title: 'Added' }]);
    store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 12, ops: [{ op: 'area_note_upsert', id: 17, area_id: 'a', archived_at: '2026-09-21' }] }));
    expect((selectPlanningState(store.getState()).areas.a as UnknownRecord).notes).toEqual([]);
  });
});

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { UnknownRecord } from '../../protocol';
import { toAgentViewModel, type AgentViewModel } from './model';
import { useWorktreeToolbar } from './useWorktreeToolbar';
import { WorktreeOperationDialog } from './WorktreeOperationDialog';
const raw = { id: 'qa', name: 'QA worker', group: 'Foundation', kind: 'worker', directory: '/repo', session_id: 'reviewed', worktree_path: '' };
const success = { type: 'worktree_create', id: 'qa', ok: true, created: true, worktree_path: '/repo/.torque/worktrees/qa', relaunched: true, session_id: 'new-session', message: 'Worktree created and agent relaunched' };
function Harness({ agent, active }: { agent: AgentViewModel; active: boolean }) {
  const controller = useWorktreeToolbar(active);
  return <><button onClick={() => controller.begin(agent, 'create')}>Toolbar create</button><button onClick={() => controller.begin(agent, 'checkpoint')}>Toolbar checkpoint</button><WorktreeOperationDialog controller={controller} active={active} /></>;
}
function setup(value: UnknownRecord = raw) {
  const store = createAppStore();
  const snapshot = (agents: Record<string, UnknownRecord>) => ({ ...compactStateFixture, agents });
  store.dispatch(projectionActions.snapshotReceived(snapshot({ qa: value })));
  store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture));
  const element = (target: UnknownRecord, active = true) => <Provider store={store}><Harness agent={toAgentViewModel(String(target.id), target)} active={active} /></Provider>;
  const view = render(element(value));
  return { ...view, store, focus: (target: UnknownRecord) => view.rerender(element(target)), live: (next: UnknownRecord) => act(() => { store.dispatch(projectionActions.snapshotReceived(snapshot({ qa: next }))); }), hide: () => view.rerender(element(value, false)), show: () => view.rerender(element(value)) };
}
function api() {
  const calls: UnknownRecord[] = []; let outcome: (data: UnknownRecord) => Promise<unknown> = () => Promise.resolve({ ok: true, data: success });
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    const data = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as UnknownRecord; calls.push(data); const body = await outcome(data);
    return { ok: true, status: 200, json: () => Promise.resolve(body) };
  }));
  return { calls, outcome: (value: typeof outcome) => { outcome = value; } };
}
afterEach(() => { vi.unstubAllGlobals(); });
it('confirms active session loss before creation and cancellation sends nothing', async () => {
  const requests = api(); setup(); fireEvent.click(screen.getByText('Toolbar create'));
  expect(screen.getByText(/current conversation will be lost/)).toBeVisible(); expect(requests.calls).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' })); expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); expect(requests.calls).toHaveLength(0);
  fireEvent.click(screen.getByText('Toolbar create')); fireEvent.click(screen.getByRole('button', { name: 'Create and restart agent' }));
  await screen.findByText('Worktree created and agent relaunched'); expect(requests.calls).toHaveLength(1); expect(requests.calls[0]).toMatchObject({ cmd: 'worktree_create', id: 'qa', relaunch: true, expected_session_id: 'reviewed' }); expect(screen.getByText('new-session')).toBeVisible();
});
it('creates for a stopped agent without a redundant confirmation or session start', async () => {
  const requests = api(); requests.outcome(() => Promise.resolve({ ok: true, data: { ...success, relaunched: false, session_id: '', message: 'Worktree created' } }));
  setup({ ...raw, session_id: '' }); fireEvent.click(screen.getByText('Toolbar create')); await screen.findByText('Worktree created'); expect(requests.calls[0]).toMatchObject({ relaunch: false, expected_session_id: '' });
});
it('guards duplicate pending creation and retains its original target across a focus change', async () => {
  const requests = api(); let release: (value: unknown) => void = () => {};
  requests.outcome(() => new Promise((resolve) => { release = resolve; })); const view = setup();
  fireEvent.click(screen.getByText('Toolbar create')); fireEvent.click(screen.getByRole('button', { name: 'Create and restart agent' }));
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled(); fireEvent.click(screen.getByRole('button', { name: 'Create and restart agent' })); expect(requests.calls).toHaveLength(1);
  view.focus({ ...raw, id: 'other', name: 'Other worker' }); expect(screen.getByRole('dialog', { name: 'Create worktree for QA worker?' })).toBeVisible();
  await act(async () => { release({ ok: true, data: success }); await Promise.resolve(); }); await screen.findByText('Worktree created and agent relaunched'); expect(requests.calls[0]?.id).toBe('qa');
});
it('retries only relaunch after creation succeeded, preserving its known path while pending', async () => {
  const requests = api(); const view = setup(); requests.outcome(() => Promise.resolve({ ok: true, data: { ...success, ok: false, relaunched: false, session_id: '', phase: 'relaunch', resume_available: true, error: 'PTY unavailable' } }));
  fireEvent.click(screen.getByText('Toolbar create')); fireEvent.click(screen.getByRole('button', { name: 'Create and restart agent' })); await screen.findByText('PTY unavailable');
  view.live({ ...raw, session_id: '', worktree_path: success.worktree_path }); expect(screen.getByRole('button', { name: 'Retry relaunch' })).toBeEnabled();
  let release: (value: unknown) => void = () => {}; requests.outcome(() => new Promise((resolve) => { release = resolve; })); fireEvent.click(screen.getByRole('button', { name: 'Retry relaunch' }));
  expect(screen.getByText(success.worktree_path)).toBeVisible(); expect(requests.calls[1]).toMatchObject({ cmd: 'worktree_create', id: 'qa', relaunch: true, expected_session_id: '', resume_worktree_path: success.worktree_path }); expect(requests.calls[1]?.idempotency_key).not.toBe(requests.calls[0]?.idempotency_key);
  await act(async () => { release({ ok: true, data: success }); await Promise.resolve(); }); await screen.findByText('Worktree created and agent relaunched');
});
it('explicitly retries uncertain creation with the same payload despite a worktree delta and reconnect', async () => {
  const requests = api(); const view = setup(); requests.outcome(() => Promise.reject(new Error('Lost response')));
  fireEvent.click(screen.getByText('Toolbar create')); fireEvent.click(screen.getByRole('button', { name: 'Create and restart agent' })); await screen.findByText(/outcome is unknown/);
  view.live({ ...raw, worktree_path: success.worktree_path, session_id: 'new-session' }); act(() => { view.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); view.store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); }); expect(requests.calls).toHaveLength(1);
  requests.outcome(() => Promise.resolve({ ok: true, data: success })); fireEvent.click(screen.getByRole('button', { name: 'Retry operation' })); await screen.findByText('Worktree created and agent relaunched'); expect(requests.calls[1]).toEqual(requests.calls[0]);
});
it('shows a changed-session warning and binds the explicit confirmation to the current session', async () => {
  const requests = api(); const view = setup(); fireEvent.click(screen.getByText('Toolbar create'));
  view.live({ ...raw, session_id: 'replacement' }); expect(screen.getByText(/session has changed since this dialog opened/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Create and restart agent' })); await screen.findByText('Worktree created and agent relaunched'); expect(requests.calls[0]?.expected_session_id).toBe('replacement');
});
it('rejects an incomplete creation or unconfirmed requested relaunch', async () => {
  const requests = api(); setup(); requests.outcome(() => Promise.resolve({ ok: true, data: { ...success, relaunched: false } }));
  fireEvent.click(screen.getByText('Toolbar create')); fireEvent.click(screen.getByRole('button', { name: 'Create and restart agent' })); await screen.findByText(/requested relaunch was not confirmed/); expect(screen.getByRole('button', { name: 'Retry operation' })).toBeEnabled(); expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
});
it('reports toolbar checkpoint refusal and a subsequent acknowledged no-op for the original target', async () => {
  const requests = api(); const view = setup({ ...raw, worktree_path: success.worktree_path }); requests.outcome(() => Promise.resolve({ ok: false, error: 'Checkpoint refused' }));
  fireEvent.click(screen.getByText('Toolbar checkpoint')); await screen.findByText('Checkpoint refused'); view.focus({ ...raw, id: 'other', name: 'Other worker' });
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_checkpoint', id: 'qa', ok: true, created: false, sha: '', message: 'No changes to checkpoint' } }));
  fireEvent.click(screen.getByRole('button', { name: 'Retry checkpoint' })); await screen.findByText('No changes to checkpoint'); expect(requests.calls[1]?.id).toBe('qa'); expect(requests.calls[1]?.idempotency_key).not.toBe(requests.calls[0]?.idempotency_key);
});
it('does not submit new creation for a deleted target and retains results across temporary hiding', () => {
  const requests = api(); const view = setup(); fireEvent.click(screen.getByText('Toolbar create')); view.live({ ...raw, deleted_at: 1 }); expect(screen.getByRole('button', { name: 'Create worktree' })).toBeDisabled(); expect(requests.calls).toHaveLength(0);
  view.hide(); expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); view.show(); expect(screen.getByRole('dialog', { name: 'Create worktree for QA worker?' })).toBeVisible();
});
it('prevents starting a toolbar operation before synchronization', async () => {
  const requests = api(); const view = setup(); act(() => { view.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); });
  fireEvent.click(screen.getByText('Toolbar create')); await waitFor(() => expect(requests.calls).toHaveLength(0)); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

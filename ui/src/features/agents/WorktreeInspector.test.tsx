import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { UnknownRecord } from '../../protocol';
import { toAgentViewModel } from './model';
import { WorktreeInspector } from './WorktreeInspector';
const agent = toAgentViewModel('qa', { id: 'qa', name: 'QA', kind: 'worker', worktree_path: '/tmp/qa', worktree_branch: 'qa' });
const frames: Record<string, UnknownRecord> = {
  worktree_diff_full: { type: 'worktree_diff_full', id: 'qa', files: [{ path: 'keep.txt', hunks: [{ header: '@@ first @@', lines: [{ type: 'add', text: 'retained line' }] }] }] },
  worktree_check_merge: { type: 'worktree_check_merge', id: 'qa', clean: true, default_message: 'Suggested message' },
  worktree_history: { type: 'worktree_history', id: 'qa', commits: [{ sha: 'abc', message: 'Retained checkpoint' }] },
};
function setup() {
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture));
  const props = { agent, sendCommand: vi.fn(() => true), onUnavailable: vi.fn(), onClose: vi.fn() };
  const element = (responses: Record<string, unknown>, active = true, target: ReturnType<typeof toAgentViewModel> | null = agent) => <Provider store={store}><WorktreeInspector {...props} agent={target} responses={responses} active={active} /></Provider>;
  const cached = Object.fromEntries(Object.entries(frames).map(([key, value]) => [`${key}:qa`, value]));
  const view = render(element(cached)); return { store, ...props, ...view, update: (responses: Record<string, unknown>, active = true, target: ReturnType<typeof toAgentViewModel> | null = agent) => view.rerender(element(responses, active, target)) };
}
function api() {
  let failure = ''; let hold = false; const values = { ...frames }; const pending: (() => void)[] = [];
  const calls: { data: UnknownRecord; signal: AbortSignal }[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
    const data = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as UnknownRecord; calls.push({ data, signal: options.signal as AbortSignal });
    const payload = failure ? { ok: false, error: failure } : { ok: true, data: values[String(data.cmd)] };
    const response = { ok: true, json: () => Promise.resolve(payload) };
    return hold ? new Promise((resolve) => pending.push(() => resolve(response))) : Promise.resolve(response);
  }));
  return { calls, fail: (value: string) => { failure = value; }, frame: (command: string, value: UnknownRecord) => { values[command] = value; }, hold: (value: boolean) => { hold = value; }, release: () => { pending.splice(0).forEach((reply) => reply()); } };
}
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
it('keeps accepted diff nodes and merge drafts when compact resync clears the auxiliary cache', async () => {
  const requests = api(); const view = setup(); const region = await screen.findByRole('region', { name: 'Worktree diff files' });
  const message = screen.getByRole('textbox', { name: 'Merge message' }); fireEvent.change(message, { target: { value: 'My draft' } }); message.focus(); (message as HTMLTextAreaElement).setSelectionRange(1, 4);
  view.update({});
  expect(screen.getByRole('region', { name: 'Worktree diff files' })).toBe(region); expect(message).toHaveValue('My draft'); expect(message).toHaveFocus();
  await waitFor(() => expect(requests.calls).toHaveLength(3));
  requests.fail('Read refused'); act(() => { view.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); view.store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); });
  await waitFor(() => expect(requests.calls).toHaveLength(6)); await screen.findAllByText(/Read refused/, { exact: false });
  expect(screen.getByRole('region', { name: 'Worktree diff files' })).toBe(region); expect(screen.getByRole('button', { name: 'Create PR & merge' })).toBeDisabled();
  requests.fail(''); fireEvent.click(screen.getByRole('button', { name: 'Retry changes' })); await waitFor(() => expect(screen.queryAllByRole('alert')).toHaveLength(0)); expect(screen.getByRole('button', { name: 'Create PR & merge' })).toBeEnabled(); expect(requests.calls).toHaveLength(9);
});

it('keeps bounded diff disclosure across tabs, refresh and temporary hiding', async () => {
  const requests = api(); const diff = (count: number) => ({ ...frames.worktree_diff_full, files: [{ path: 'large.txt', hunks: [{ header: '@@ large @@', lines: Array.from({ length: count }, (_, index) => ({ type: 'add', text: `line ${index}` })) }] }] });
  requests.frame('worktree_diff_full', diff(650)); const view = setup(); const region = await screen.findByRole('region');
  fireEvent.click(screen.getByRole('button', { name: 'Show 250 more lines' })); const nav = screen.getByRole('navigation', { name: 'Worktree views' });
  fireEvent.click(within(nav).getByRole('button', { name: /History/ })); expect(region).not.toBeVisible(); fireEvent.click(within(nav).getByRole('button', { name: 'Changes' })); expect(screen.getByRole('region')).toBe(region);
  requests.frame('worktree_diff_full', diff(655)); fireEvent.click(within(nav).getByRole('button', { name: 'Refresh' })); await waitFor(() => expect(region.querySelectorAll('pre span')).toHaveLength(655));
  view.update({}, false); expect(requests.calls.every((call) => call.signal.aborted)).toBe(true); expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); expect(requests.calls).toHaveLength(6);
  view.update({}, true); await waitFor(() => expect(requests.calls).toHaveLength(9)); expect((await screen.findByRole('region')).querySelectorAll('pre span')).toHaveLength(655);
});
it('rejects mismatched and malformed reads independently while retaining previous data', async () => {
  const requests = api(); setup(); const region = await screen.findByRole('region');
  requests.frame('worktree_diff_full', { ...frames.worktree_diff_full, id: 'other' }); requests.frame('worktree_history', { ...frames.worktree_history, commits: null }); requests.frame('worktree_check_merge', { ...frames.worktree_check_merge, error: 'Changed boundary' });
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' })); await screen.findByText(/Changed boundary/); await screen.findByText(/response did not match/); await screen.findByText(/response was incomplete/);
  expect(screen.getByRole('region')).toBe(region); expect(screen.getByRole('button', { name: 'Create PR' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: /History 1/ })); expect(screen.getByText('Retained checkpoint')).toBeVisible();
});
it('cancels obsolete target reads and ignores replies after replacement or unmount', async () => {
  const requests = api(); requests.hold(true); const view = setup(); expect(requests.calls).toHaveLength(3);
  const next = { ...agent, id: 'second', name: 'Second', worktreePath: '/tmp/second' };
  for (const command of Object.keys(frames)) requests.frame(command, { ...frames[command], id: 'second' });
  requests.hold(false); view.update({}, true, next); await screen.findByRole('region'); expect(requests.calls.slice(0, 3).every((call) => call.signal.aborted)).toBe(true);
  await act(async () => { requests.release(); await Promise.resolve(); }); expect(screen.queryByRole('alert')).not.toBeInTheDocument(); expect(screen.getByRole('dialog', { name: 'Second worktree' })).toBeVisible();
  requests.hold(true); fireEvent.click(screen.getByRole('button', { name: 'Refresh' })); view.unmount(); expect(requests.calls.every((call) => call.signal.aborted)).toBe(true); await act(async () => { requests.release(); await Promise.resolve(); });
});
it('bounds stalled reads and retains accepted content until retry succeeds', async () => {
  const requests = api(); setup(); const region = await screen.findByRole('region'); requests.hold(true); vi.useFakeTimers();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' })); await act(async () => { await vi.advanceTimersByTimeAsync(30_001); });
  expect(screen.getAllByRole('alert')).toHaveLength(3); expect(screen.getByRole('region')).toBe(region); expect(requests.calls.slice(-3).every((call) => call.signal.aborted)).toBe(true); vi.useRealTimers();
  requests.hold(false); fireEvent.click(screen.getByRole('button', { name: 'Retry preflight' })); await waitFor(() => expect(screen.queryAllByRole('alert')).toHaveLength(0));
  await act(async () => { requests.release(); await Promise.resolve(); }); expect(screen.queryAllByRole('alert')).toHaveLength(0);
});

it('shows retryable unavailable states after an initial read refusal', async () => {
  const requests = api(); requests.fail('Initial refusal'); setup(); await screen.findByText('Changes unavailable'); expect(screen.getByText('Merge preflight unavailable')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /History 0/ })); expect(screen.getByText('History unavailable')).toBeVisible(); expect(screen.queryByText('Loading history')).not.toBeInTheDocument();
  requests.fail(''); fireEvent.click(screen.getByRole('button', { name: 'Retry history' })); await screen.findByText('Retained checkpoint'); expect(screen.queryAllByRole('alert')).toHaveLength(0);
});

function mutationApi() {
  const calls: UnknownRecord[] = [];
  let outcome: (data: UnknownRecord, signal: AbortSignal) => Promise<unknown> = () => Promise.resolve({ ok: false, error: 'Refused' });
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    const data = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as UnknownRecord; calls.push(data);
    const payload = frames[String(data.cmd)] ? { ok: true, data: frames[String(data.cmd)] } : await outcome(data, options.signal as AbortSignal);
    return { ok: true, status: 200, json: () => Promise.resolve(payload) };
  }));
  return { calls, outcome: (next: typeof outcome) => { outcome = next; }, writes: () => calls.filter((data) => !frames[String(data.cmd)]) };
}
it('retains deletion confirmation during pending and refused writes and dismisses only a matching removal', async () => {
  const requests = mutationApi(); const view = setup(); await screen.findByRole('region');
  let release: (value: unknown) => void = () => {};
  requests.outcome(() => new Promise((resolve) => { release = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete worktree…' })); fireEvent.click(screen.getByRole('button', { name: 'Delete worktree' }));
  expect(screen.getByRole('button', { name: 'Close' })).toBeDisabled(); expect(screen.getByRole('button', { name: 'Delete worktree' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Delete worktree' })); expect(requests.writes()).toHaveLength(1); expect(view.onClose).not.toHaveBeenCalled();
  await act(async () => { release({ ok: false, error: 'Active worktree cannot be removed' }); await Promise.resolve(); }); await screen.findByText('Active worktree cannot be removed');
  expect(view.onClose).not.toHaveBeenCalled(); await waitFor(() => expect(screen.getByRole('button', { name: 'Delete worktree' })).toBeEnabled());
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_remove', id: 'qa', worktree_removed: true } }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete worktree' })); await waitFor(() => expect(view.onClose).toHaveBeenCalledTimes(1));
  expect(requests.writes()[1]?.idempotency_key).not.toBe(requests.writes()[0]?.idempotency_key);
});
it('retains an uncertain operation and retries its exact payload without replaying on reconnect', async () => {
  const requests = mutationApi(); const view = setup(); await screen.findByRole('region');
  fireEvent.change(screen.getByRole('textbox', { name: 'Merge message' }), { target: { value: 'Keep this merge intent' } });
  requests.outcome(() => Promise.reject(new TypeError('Connection lost'))); fireEvent.click(screen.getByRole('button', { name: 'Create PR & merge' }));
  await screen.findByText(/The outcome is unknown/); expect(screen.getByRole('textbox', { name: 'Merge message' })).toHaveValue('Keep this merge intent'); expect(screen.getByRole('button', { name: 'Checkpoint' })).toBeDisabled();
  act(() => { view.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); view.store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); }); expect(requests.writes()).toHaveLength(1);
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_merge', id: 'qa', ok: true, pending: true, merged: false, url: 'https://example.invalid/pr/1', message: 'Waiting for required checks' } }));
  fireEvent.click(screen.getByRole('button', { name: 'Retry operation' })); await screen.findByText('Waiting for required checks'); expect(requests.writes()[1]).toEqual(requests.writes()[0]); expect(view.onClose).not.toHaveBeenCalled(); expect(screen.getByRole('link', { name: 'https://example.invalid/pr/1' })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create PR & merge' })).toBeEnabled());
});
it('rejects mismatched and incomplete acknowledgements without treating a removal as complete', async () => {
  const requests = mutationApi(); const view = setup(); await screen.findByRole('region');
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_remove', id: 'other', worktree_removed: true } }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete worktree…' })); fireEvent.click(screen.getByRole('button', { name: 'Delete worktree' })); await screen.findByText(/did not match/);
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_remove', id: 'qa' } })); fireEvent.click(screen.getByRole('button', { name: 'Retry operation' })); await screen.findByText(/removal was not confirmed/); expect(view.onClose).not.toHaveBeenCalled(); expect(requests.writes()[1]).toEqual(requests.writes()[0]);
});
it('refreshes reads after checkpoint and ignores cached terminal merge progress', async () => {
  const requests = mutationApi(); const view = setup(); await screen.findByRole('region');
  view.update({ 'worktree_merge_progress:qa': { phase: 'done', message: 'Old completed operation' }, 'worktree_pr:qa': { url: 'https://example.invalid/old' } });
  expect(screen.getByRole('button', { name: 'Create PR & merge' })).toBeEnabled(); expect(screen.queryByText('Old completed operation')).not.toBeInTheDocument();
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_checkpoint', id: 'qa', ok: true, created: false, sha: '', message: 'No changes to checkpoint' } }));
  fireEvent.click(screen.getByRole('button', { name: 'Checkpoint' })); await screen.findByText('No changes to checkpoint');
  await waitFor(() => expect(requests.calls.filter((data) => frames[String(data.cmd)])).toHaveLength(6));
  expect(screen.queryByRole('link', { name: 'https://example.invalid/old' })).not.toBeInTheDocument();
});
it('keeps rollback confirmation and errors inside the modal until the selected checkpoint is acknowledged', async () => {
  const requests = mutationApi();
  const prior = frames.worktree_history!; frames.worktree_history = { ...prior, commits: [{ sha: 'new', message: 'New checkpoint' }, { sha: 'old', message: 'Old checkpoint' }] };
  try {
    setup(); await screen.findByRole('region'); fireEvent.click(screen.getByRole('button', { name: /History 2/ })); fireEvent.click(screen.getByRole('button', { name: 'Rollback…' }));
    const modal = screen.getByRole('dialog', { name: 'Rollback worktree?' }); fireEvent.click(within(modal).getByRole('button', { name: 'Rollback' })); await within(modal).findByText('Refused');
    await waitFor(() => expect(within(modal).getByRole('button', { name: 'Rollback' })).toBeEnabled());
    requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_rollback', id: 'qa', ok: true, sha: 'wrong' } })); fireEvent.click(within(modal).getByRole('button', { name: 'Rollback' })); await within(modal).findByText(/did not match/); expect(modal).toBeVisible();
    requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_rollback', id: 'qa', ok: true, sha: 'old' } })); fireEvent.click(within(modal).getByRole('button', { name: 'Retry rollback' })); await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Rollback worktree?' })).not.toBeInTheDocument());
    expect(requests.writes().map((data) => data.sha)).toEqual(['old', 'old', 'old']); expect(requests.writes()[2]).toEqual(requests.writes()[1]);
  } finally { frames.worktree_history = prior; }
});
it('bounds a stalled write and offers an explicit retry without discarding the draft', async () => {
  const requests = mutationApi(); setup(); await screen.findByRole('region');
  requests.outcome((_data, signal) => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('Aborted')))));
  vi.useFakeTimers(); fireEvent.click(screen.getByRole('button', { name: 'Checkpoint' })); await act(async () => { await vi.advanceTimersByTimeAsync(120_001); });
  expect(screen.getByRole('alert')).toHaveTextContent('request timed out'); expect(screen.getByRole('button', { name: 'Retry operation' })).toBeEnabled(); expect(requests.writes()).toHaveLength(1); vi.useRealTimers();
});
it('displays typed rebase refusal and permits a new keyed attempt after the refusal', async () => {
  const requests = mutationApi(); const prior = frames.worktree_check_merge!; frames.worktree_check_merge = { ...prior, stale_base: true };
  try {
    setup(); await screen.findByRole('button', { name: 'Rebase onto base' }); requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_rebase', id: 'qa', ok: false, error: 'Resolve conflicts first' } }));
    fireEvent.click(screen.getByRole('button', { name: 'Rebase onto base' })); await screen.findByText('Resolve conflicts first'); await waitFor(() => expect(screen.getByRole('button', { name: 'Rebase onto base' })).toBeEnabled());
    requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_rebase', id: 'qa', ok: true } })); fireEvent.click(screen.getByRole('button', { name: 'Rebase onto base' })); await screen.findByText('Worktree rebased.'); expect(requests.writes()[1]?.idempotency_key).not.toBe(requests.writes()[0]?.idempotency_key);
  } finally { frames.worktree_check_merge = prior; }
});
it('shows PR refusals and pending nested PR outcomes without dismissing the inspector', async () => {
  const requests = mutationApi(); const view = setup(); await screen.findByRole('region');
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_pr', id: 'qa', ok: false, error: 'No remote configured' } }));
  fireEvent.click(screen.getByRole('button', { name: 'Create PR' })); await screen.findByText('No remote configured'); await waitFor(() => expect(screen.getByRole('button', { name: 'Create PR' })).toBeEnabled());
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_pr', id: 'qa', ok: true, pending: true, pending_ee_pr: true, url: 'https://example.invalid/nested', message: 'Nested PR requires review' } }));
  fireEvent.click(screen.getByRole('button', { name: 'Create PR' })); await screen.findByText('Nested PR requires review'); expect(screen.getByRole('link', { name: 'https://example.invalid/nested' })).toBeInTheDocument(); expect(view.onClose).not.toHaveBeenCalled();
});
it('keeps merge cleanup warnings visible after the successful merge acknowledgement', async () => {
  const requests = mutationApi(); const view = setup(); await screen.findByRole('region');
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_merge', id: 'qa', ok: true, warning: 'Merged but worktree remains', cleanup: { errors: ['Branch deletion refused'] } } }));
  fireEvent.click(screen.getByRole('button', { name: 'Create PR & merge' })); await screen.findByText('Merged but worktree remains'); expect(screen.getByText('Cleanup needs attention: Branch deletion refused')).toBeInTheDocument(); expect(view.onClose).not.toHaveBeenCalled();
});

it('retains the submitted target and cleanup result when merge closes the live agent before acknowledgement', async () => {
  const requests = mutationApi(); const view = setup(); await screen.findByRole('region');
  let release: (value: unknown) => void = () => {};
  requests.outcome(() => new Promise((resolve) => { release = resolve; }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Close agent after merge' })); fireEvent.click(screen.getByRole('button', { name: 'Create PR & merge' }));
  view.update({}, true, null); expect(screen.getByRole('region', { name: 'Worktree diff files' })).toBeVisible(); expect(screen.getByRole('dialog', { name: 'QA worktree' })).toBeVisible(); expect(screen.getByRole('button', { name: 'Close' })).toBeDisabled();
  await act(async () => { release({ ok: true, data: { type: 'worktree_merge', id: 'qa', ok: true, warning: 'Merged; cleanup needs review' } }); await Promise.resolve(); });
  await screen.findByText('Merged; cleanup needs review'); expect(screen.getByRole('button', { name: 'Checkpoint' })).toBeDisabled(); expect(screen.getByRole('button', { name: 'Close' })).toBeEnabled(); expect(requests.writes()[0]).toMatchObject({ close_agent_on_merge: true });
});

it('retains valid blocked preflight data so stale-base recovery remains available', async () => {
  const requests = api(); requests.frame('worktree_check_merge', { type: 'worktree_check_merge', id: 'qa', clean: false, stale_base: true, error: 'Base advanced; rebase required', conflicts: [{ path: 'sample.txt', reason: 'Base changed' }] }); setup();
  await screen.findByText('Base advanced; rebase required'); expect(screen.getByRole('button', { name: 'Rebase onto base' })).toBeEnabled(); expect(screen.getByRole('button', { name: 'Create PR & merge' })).toBeDisabled(); expect(screen.getByRole('button', { name: 'Create PR' })).toBeDisabled(); expect(screen.getByText('Base changed')).toBeInTheDocument(); expect(screen.queryByRole('button', { name: 'Retry preflight' })).not.toBeInTheDocument();
});

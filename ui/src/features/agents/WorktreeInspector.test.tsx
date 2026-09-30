import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { UnknownRecord } from '../../protocol';
import { toAgentViewModel } from './model';
import { WorktreeInspector } from './WorktreeInspector';
const agent = toAgentViewModel('qa', { id: 'qa', name: 'QA', group: 'qa-group', kind: 'worker', worktree_path: '/tmp/qa', worktree_branch: 'qa' });
const removalReview = { path: '/tmp/qa', branch: 'qa', repo_root: '/tmp', session_id: '', mode: 'remove', shared_ids: [], dirty: false, ignored_files: false, head: 'head', base_head: 'base', changes_digest: 'digest', checkpoints: 0 };
const frames: Record<string, UnknownRecord> = {
  worktree_remove_preview: { type: 'worktree_remove_preview', id: 'qa', ok: true, review: removalReview, shared_with: [], blocked_reason: '' },
  worktree_diff_full: { type: 'worktree_diff_full', id: 'qa', files: [{ path: 'keep.txt', hunks: [{ header: '@@ first @@', lines: [{ type: 'add', text: 'retained line' }] }] }] },
  worktree_check_merge: { type: 'worktree_check_merge', id: 'qa', clean: true, default_message: 'Suggested message' },
  worktree_history: { type: 'worktree_history', id: 'qa', commits: [{ sha: 'abc', message: 'Retained checkpoint' }] },
};
function setup(settings: UnknownRecord = {}) {
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, group_settings: { 'qa-group': settings } })); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture));
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
  fireEvent.click(screen.getByRole('button', { name: 'Delete worktree…' })); await waitFor(() => expect(screen.getByRole('button', { name: 'Delete worktree' })).toBeEnabled()); fireEvent.click(screen.getByRole('button', { name: 'Delete worktree' }));
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled(); expect(screen.getByRole('button', { name: 'Delete worktree' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Delete worktree' })); expect(requests.writes()).toHaveLength(1); expect(view.onClose).not.toHaveBeenCalled();
  await act(async () => { release({ ok: false, error: 'Active worktree cannot be removed' }); await Promise.resolve(); }); await screen.findByText('Active worktree cannot be removed');
  expect(view.onClose).not.toHaveBeenCalled(); await waitFor(() => expect(screen.getByRole('button', { name: 'Delete worktree' })).toBeEnabled());
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_remove', id: 'qa', ok: true, mode: 'remove', worktree_path: '/tmp/qa', branch_deleted: true, worktree_removed: true } }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete worktree' })); await screen.findByRole('button', { name: 'Done' }); expect(view.onClose).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('button', { name: 'Done' })); expect(view.onClose).toHaveBeenCalledTimes(1);
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
  fireEvent.click(screen.getByRole('button', { name: 'Delete worktree…' })); await waitFor(() => expect(screen.getByRole('button', { name: 'Delete worktree' })).toBeEnabled()); fireEvent.click(screen.getByRole('button', { name: 'Delete worktree' })); await screen.findByText(/did not match/);
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_remove', id: 'qa', ok: true, mode: 'remove', worktree_path: '/tmp/qa' } })); fireEvent.click(screen.getByRole('button', { name: 'Retry removal' })); await screen.findByText(/removal was not confirmed/); expect(view.onClose).not.toHaveBeenCalled(); expect(requests.writes()[1]).toEqual(requests.writes()[0]);
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
  fireEvent.click(screen.getByRole('button', { name: 'Create PR' })); const review = screen.getByRole('dialog', { name: 'Create pull request?' }); fireEvent.click(within(review).getByRole('button', { name: 'Push branch and create PR' })); await within(review).findByText('No remote configured'); await waitFor(() => expect(within(review).getByRole('button', { name: 'Push branch and create PR' })).toBeEnabled());
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_pr', id: 'qa', ok: true, pending: true, pending_ee_pr: true, url: 'https://example.invalid/nested', message: 'Nested PR requires review' } }));
  fireEvent.click(within(review).getByRole('button', { name: 'Push branch and create PR' })); await screen.findByText('Nested PR requires review'); expect(screen.getByRole('link', { name: 'https://example.invalid/nested' })).toBeInTheDocument(); expect(view.onClose).not.toHaveBeenCalled();
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

it('reviews PR branch, base and push before any write and cancels without sending', async () => {
  const requests = mutationApi(); setup(); await screen.findByRole('region');
  fireEvent.click(screen.getByRole('button', { name: 'Create PR' }));
  const dialog = screen.getByRole('dialog', { name: 'Create pull request?' });
  expect(within(dialog).getByText(/pushed to origin/)).toBeVisible();
  expect(within(dialog).getByText('qa', { exact: true })).toBeVisible();
  expect(within(dialog).getByText('main', { exact: true })).toBeVisible();
  expect(requests.writes()).toHaveLength(0);
  await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus());
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByRole('dialog', { name: 'Create pull request?' })).not.toBeInTheDocument(); expect(requests.writes()).toHaveLength(0); expect(screen.getByRole('button', { name: 'Create PR' })).toHaveFocus();
});
it.each([
  ['keep', false, false], ['close', true, false], ['remove', false, true], ['close_remove', true, true], ['auto_sweep', true, true],
])('inherits %s cleanup and preserved diff from the target group', async (mode, close, remove) => {
  const requests = mutationApi(); setup({ worktree_merge_cleanup: mode, worktree_merge_preserve_diff: true }); await screen.findByRole('region');
  expect(screen.getByRole('checkbox', { name: 'Close agent after merge' })).toHaveProperty('checked', close);
  expect(screen.getByRole('checkbox', { name: 'Delete worktree after merge' })).toHaveProperty('checked', remove);
  expect(screen.getByRole('checkbox', { name: 'Preserve boundary diff' })).toBeChecked();
  expect(screen.getByText(/Cleanup options run only after/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Create PR & merge' }));
  expect(requests.writes()[0]).toMatchObject({ close_agent_on_merge: close, remove_worktree_on_merge: remove, preserve_merge_diff: true });
  await screen.findByText('Refused');
});

it('keeps PR confirmation through pending and lost replies and retries the exact operation after reconnect', async () => {
  const requests = mutationApi(); const view = setup(); await screen.findByRole('region');
  let reject: (error: Error) => void = () => {};
  requests.outcome(() => new Promise((_resolve, fail) => { reject = fail; }));
  fireEvent.click(screen.getByRole('button', { name: 'Create PR' })); const dialog = screen.getByRole('dialog', { name: 'Create pull request?' });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Push branch and create PR' }));
  expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeDisabled(); expect(within(dialog).getByRole('button', { name: 'Push branch and create PR' })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Push branch and create PR' })); expect(requests.writes()).toHaveLength(1);
  await act(async () => { reject(new TypeError('Lost response')); await Promise.resolve(); }); await within(dialog).findByText(/outcome is unknown/);
  view.update({}, false); expect(screen.queryByRole('dialog', { name: 'Create pull request?' })).not.toBeInTheDocument();
  act(() => { view.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); view.store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); }); view.update({}, true);
  expect(requests.writes()).toHaveLength(1); const restored = await screen.findByRole('dialog', { name: 'Create pull request?' });
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_pr', id: 'qa', ok: true, url: 'https://example.invalid/pr/qa', message: 'PR created' } }));
  fireEvent.click(within(restored).getByRole('button', { name: 'Retry PR operation' })); await screen.findByText('PR created');
  expect(requests.writes()[1]).toEqual(requests.writes()[0]); expect(screen.queryByRole('dialog', { name: 'Create pull request?' })).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'https://example.invalid/pr/qa' })).toBeVisible();
});
it('requires fresh PR review when the branch or base changes while confirmation is open', async () => {
  const requests = mutationApi(); const view = setup(); await screen.findByRole('region'); fireEvent.click(screen.getByRole('button', { name: 'Create PR' }));
  const changed = { ...agent, worktreeBranch: 'replacement', raw: { ...agent.raw, worktree_base_branch: 'release' } }; view.update({}, true, changed);
  const dialog = screen.getByRole('dialog', { name: 'Create pull request?' }); expect(within(dialog).getByRole('alert')).toHaveTextContent('target has changed');
  expect(within(dialog).getByRole('button', { name: 'Push branch and create PR' })).toBeDisabled(); expect(within(dialog).getByText('qa', { exact: true })).toBeVisible(); expect(requests.writes()).toHaveLength(0);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' })); await waitFor(() => expect(screen.getByRole('button', { name: 'Create PR' })).toBeEnabled()); fireEvent.click(screen.getByRole('button', { name: 'Create PR' }));
  const current = screen.getByRole('dialog', { name: 'Create pull request?' }); expect(within(current).getByText('replacement', { exact: true })).toBeVisible(); expect(within(current).getByText('release', { exact: true })).toBeVisible(); expect(requests.writes()).toHaveLength(0);
});
it('refreshes untouched merge defaults but retains edits and submitted options through reconnect and refusal', async () => {
  const requests = mutationApi(); const view = setup({ worktree_merge_cleanup: 'close_remove', worktree_merge_preserve_diff: true }); await screen.findByRole('region');
  const close = screen.getByRole('checkbox', { name: 'Close agent after merge' }); fireEvent.click(close);
  const updateDefaults = (mode: string, preserve: boolean) => view.store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, group_settings: { 'qa-group': { worktree_merge_cleanup: mode, worktree_merge_preserve_diff: preserve } } }));
  act(() => { updateDefaults('keep', false); }); expect(close).not.toBeChecked(); expect(screen.getByRole('checkbox', { name: 'Delete worktree after merge' })).not.toBeChecked(); expect(screen.getByRole('checkbox', { name: 'Preserve boundary diff' })).not.toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Preserve boundary diff' }));
  act(() => { updateDefaults('close_remove', false); view.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); view.store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create PR & merge' })).toBeEnabled()); expect(close).not.toBeChecked(); expect(screen.getByRole('checkbox', { name: 'Delete worktree after merge' })).toBeChecked(); expect(screen.getByRole('checkbox', { name: 'Preserve boundary diff' })).toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: 'Create PR & merge' })); await screen.findByText('Refused');
  act(() => { updateDefaults('keep', false); }); expect(screen.getByRole('checkbox', { name: 'Delete worktree after merge' })).toBeChecked(); expect(screen.getByRole('checkbox', { name: 'Preserve boundary diff' })).toBeChecked();
  expect(requests.writes()[0]).toMatchObject({ close_agent_on_merge: false, remove_worktree_on_merge: true, preserve_merge_diff: true });
});

it('reviews shared-link consequences, dirty commits and stopped-session behavior without writing on cancel', async () => {
  const prior = frames.worktree_remove_preview!; frames.worktree_remove_preview = { ...prior, review: { ...removalReview, mode: 'unlink', shared_ids: ['peer'], dirty: true, checkpoints: 3 }, shared_with: [{ id: 'peer', name: 'Other worker' }] };
  try {
    const requests = mutationApi(); setup(); const diff = await screen.findByRole('region'); fireEvent.click(screen.getByRole('button', { name: 'Delete worktree…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Remove worktree link?' }); expect(within(dialog).getByText(/Only this agent's link/)).toBeVisible(); expect(within(dialog).getByText(/Other worker/)).toBeVisible(); expect(within(dialog).getByText(/uncommitted changes/)).toBeVisible(); expect(within(dialog).getByText(/3 commits ahead of its base/)).toBeVisible(); expect(within(dialog).getByText(/will not start or restart/)).toBeVisible();
    expect(requests.writes()).toHaveLength(0); fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' })); expect(screen.getByRole('region')).toBe(diff); expect(requests.writes()).toHaveLength(0);
  } finally { frames.worktree_remove_preview = prior; }
});
it('refuses to accept filesystem deletion for a reviewed unlink and retries the original link operation', async () => {
  const prior = frames.worktree_remove_preview!; const review = { ...removalReview, mode: 'unlink', shared_ids: ['peer'] }; frames.worktree_remove_preview = { ...prior, review, shared_with: [{ id: 'peer', name: 'Peer' }] };
  try {
    const requests = mutationApi(); const view = setup(); await screen.findByRole('region'); fireEvent.click(screen.getByRole('button', { name: 'Delete worktree…' })); await screen.findByRole('button', { name: 'Remove link' });
    requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_remove', id: 'qa', ok: true, mode: 'unlink', worktree_path: '/tmp/qa', link_cleared: true, worktree_removed: true } }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove link' })); await screen.findByText(/Link-only removal was not confirmed/); expect(view.onClose).not.toHaveBeenCalled(); expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    view.update({}, true, { ...agent, worktreePath: '' }); expect(screen.getByText(/Only this agent's link/)).toBeVisible();
    requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_remove', id: 'qa', ok: true, mode: 'unlink', worktree_path: '/tmp/qa', link_cleared: true, worktree_removed: false } }));
    fireEvent.click(screen.getByRole('button', { name: 'Retry removal' })); await screen.findByRole('button', { name: 'Done' }); expect(view.onClose).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('button', { name: 'Done' })); expect(view.onClose).toHaveBeenCalledTimes(1); expect(requests.writes()[1]).toEqual(requests.writes()[0]); expect(requests.writes()[0]).toMatchObject({ removal_review: review, relaunch: false });
  } finally { frames.worktree_remove_preview = prior; }
});
it('shows active-use refusal before any removal and refreshes after the session stops', async () => {
  const prior = frames.worktree_remove_preview!; frames.worktree_remove_preview = { ...prior, review: { ...removalReview, session_id: 'live' }, blocked_reason: 'Active/fresh agent must stop first' };
  try {
    const requests = mutationApi(); setup(); await screen.findByRole('region'); fireEvent.click(screen.getByRole('button', { name: 'Delete worktree…' })); await screen.findByText('Active/fresh agent must stop first'); expect(screen.getByText(/has an attached session/)).toBeVisible(); expect(screen.getByRole('button', { name: 'Delete worktree' })).toBeDisabled(); expect(requests.writes()).toHaveLength(0);
    frames.worktree_remove_preview = prior; fireEvent.click(screen.getByRole('button', { name: 'Refresh review' })); await screen.findByText(/will not start or restart/); expect(screen.getByRole('button', { name: 'Delete worktree' })).toBeEnabled(); expect(requests.writes()).toHaveLength(0);
  } finally { frames.worktree_remove_preview = prior; }
});
it('retains removal context through failed reconnect review and requires a fresh matching review to write', async () => {
  const requests = api(); const view = setup(); await screen.findByRole('region'); fireEvent.click(screen.getByRole('button', { name: 'Delete worktree…' })); await screen.findByText(/will not start or restart/);
  requests.frame('worktree_remove_preview', { ...frames.worktree_remove_preview, id: 'different' });
  act(() => { view.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); view.store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); });
  await screen.findByText(/Removal review did not match/); expect(screen.getByText(/will not start or restart/)).toBeVisible(); expect(screen.getByRole('button', { name: 'Delete worktree' })).toBeDisabled();
  requests.frame('worktree_remove_preview', { ...frames.worktree_remove_preview, review: { ...removalReview, mode: 'unlink', shared_ids: ['peer'] }, shared_with: [{ id: 'peer', name: 'New sharing worker' }] }); fireEvent.click(screen.getByRole('button', { name: 'Refresh review' })); await screen.findByText(/New sharing worker/); expect(screen.getByRole('button', { name: 'Remove link' })).toBeEnabled();
  view.update({}, false); const before = requests.calls.length; expect(requests.calls.every((call) => call.signal.aborted)).toBe(true);
  act(() => { view.store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); view.store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); }); expect(requests.calls).toHaveLength(before);
  view.update({}, true); await screen.findByRole('dialog', { name: 'Remove worktree link?' });
});
it('times out an unavailable removal review and retries without enabling an unreviewed deletion', async () => {
  const requests = api(); setup(); await screen.findByRole('region'); requests.hold(true); vi.useFakeTimers(); fireEvent.click(screen.getByRole('button', { name: 'Delete worktree…' })); await act(async () => { await vi.advanceTimersByTimeAsync(30_001); });
  expect(screen.getByRole('alert')).toHaveTextContent('Removal review timed out'); expect(screen.getByRole('button', { name: 'Delete worktree' })).toBeDisabled(); vi.useRealTimers();
  requests.hold(false); fireEvent.click(screen.getByRole('button', { name: 'Refresh review' })); await screen.findByText(/will not start or restart/); expect(screen.getByRole('button', { name: 'Delete worktree' })).toBeEnabled();
  await act(async () => { requests.release(); await Promise.resolve(); }); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});


it('warns about discarded dirty and ignored files and retains the actual branch cleanup result', async () => {
  const prior = frames.worktree_remove_preview!;
  frames.worktree_remove_preview = { ...prior, review: { ...removalReview, dirty: true, ignored_files: true, checkpoints: 2 } };
  try {
    const requests = mutationApi(); const view = setup(); await screen.findByRole('region');
    fireEvent.click(screen.getByRole('button', { name: 'Delete worktree…' }));
    await screen.findByText(/These changes will be permanently discarded/);
    expect(screen.getByText(/Ignored files.*permanently discarded/)).toBeVisible();
    requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_remove', id: 'qa', ok: true, mode: 'remove', worktree_path: '/tmp/qa', worktree_removed: true, branch_deleted: false, message: 'Worktree removed. Branch retained: qa' } }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete worktree' }));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Worktree removed. Branch retained: qa'));
    view.update({}, true, { ...agent, worktreePath: '' });
    expect(screen.queryByRole('button', { name: 'Delete worktree' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry removal' })).not.toBeInTheDocument();
    expect(view.onClose).not.toHaveBeenCalled(); expect(requests.writes()).toHaveLength(1);
  } finally { frames.worktree_remove_preview = prior; }
});

it('submits an explicitly selected completed merge task and retains it through reconnect', async () => {
  const requests = mutationApi(); const view = setup();
  const tasks = { done: { id: 'done', task: 'Completed implementation', lane: 'Done', group: 'qa-group', agent_id: 'qa' }, peer: { id: 'peer', task: 'Peer work', lane: 'Done', group: 'qa-group', agent_id: 'other' } };
  await act(() => view.store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: tasks })));
  await screen.findByRole('region'); const selection = screen.getByRole('combobox', { name: 'Merge task' });
  expect(within(selection).getByRole('option', { name: /Completed implementation/ })).toBeInTheDocument(); expect(within(selection).queryByRole('option', { name: /Peer work/ })).not.toBeInTheDocument();
  fireEvent.change(selection, { target: { value: 'done' } }); selection.focus();
  act(() => { view.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); view.store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { ...tasks, done: { ...tasks.done, task: 'Renamed implementation' } } })); view.store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create PR & merge' })).toBeEnabled()); expect(selection).toHaveValue('done'); expect(selection).toHaveFocus();
  fireEvent.click(screen.getByRole('button', { name: 'Create PR & merge' })); expect(requests.writes()[0]).toMatchObject({ id: 'qa', merge_task_id: 'done' }); await screen.findByText('Refused'); expect(selection).toHaveValue('done');
});

it('blocks a selected merge task after reassignment without silently selecting another task', async () => {
  const requests = mutationApi(); const view = setup(); const task = { id: 'done', task: 'Completed implementation', lane: 'Done', group: 'qa-group', agent_id: 'qa' };
  await act(() => view.store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { done: task } })));
  await screen.findByRole('region'); const selection = screen.getByRole('combobox', { name: 'Merge task' }); fireEvent.change(selection, { target: { value: 'done' } });
  await act(() => view.store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { done: { ...task, agent_id: 'other' } } })));
  expect(selection).toHaveValue('done'); expect(screen.getByText('The selected task no longer belongs to this worktree. Select a current task before merging.')).toBeVisible(); expect(screen.getByRole('button', { name: 'Create PR & merge' })).toBeDisabled(); expect(requests.writes()).toHaveLength(0);
  fireEvent.change(selection, { target: { value: '' } }); expect(screen.getByRole('button', { name: 'Create PR & merge' })).toBeEnabled();
});

it('keeps explicit merge attribution fixed during an uncertain retry', async () => {
  const requests = mutationApi(); const view = setup();
  await act(() => view.store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { done: { id: 'done', task: 'Completed implementation', lane: 'Done', group: 'qa-group', agent_id: 'qa' } } })));
  await screen.findByRole('region'); const selection = screen.getByRole('combobox', { name: 'Merge task' }); fireEvent.change(selection, { target: { value: 'done' } });
  requests.outcome(() => Promise.reject(new Error('Lost reply'))); fireEvent.click(screen.getByRole('button', { name: 'Create PR & merge' })); await screen.findByRole('button', { name: 'Retry operation' }); expect(selection).toBeDisabled();
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_merge', id: 'qa', ok: false, error: 'Refused' } })); fireEvent.click(screen.getByRole('button', { name: 'Retry operation' })); await screen.findByText('Refused'); expect(requests.writes()[1]).toEqual(requests.writes()[0]); expect(requests.writes()[1]).toMatchObject({ merge_task_id: 'done' });
});


it('retains a completed merge selection without a reassignment warning after successful unlink', async () => {
  const requests = mutationApi(); const view = setup(); const task = { id: 'done', task: 'Completed implementation', lane: 'Done', group: 'qa-group', agent_id: 'qa' };
  await act(() => view.store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { done: task } })));
  await screen.findByRole('region'); fireEvent.change(screen.getByRole('combobox', { name: 'Merge task' }), { target: { value: 'done' } });
  requests.outcome(() => Promise.resolve({ ok: true, data: { type: 'worktree_merge', id: 'qa', ok: true } })); fireEvent.click(screen.getByRole('button', { name: 'Create PR & merge' })); await screen.findByText('Merge completed.');
  await act(() => view.store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { done: { ...task, agent_id: '' } } })));
  expect(screen.getByRole('combobox', { name: 'Merge task' })).toHaveValue('done'); expect(screen.queryByText('The selected task no longer belongs to this worktree. Select a current task before merging.')).not.toBeInTheDocument();
});

it('offers released tasks only with a matching open boundary recorded by this worker', async () => {
  const requests = mutationApi(); const view = setup();
  const boundary = { status: 'open', recorded_by_agent_id: 'qa', repo_root: '/repo', branch: 'qa', base_branch: 'main', commit_sha: 'head' };
  const task = { id: 'released', task: 'Reported work', lane: 'In Progress', group: 'qa-group', agent_id: '', worktree_boundary: boundary };
  const excluded = Object.fromEntries(Object.entries({ wrongWorker: { recorded_by_agent_id: 'peer' }, wrongBranch: { branch: 'peer' }, wrongRepo: { repo_root: '/peer' }, wrongBase: { base_branch: 'peer' }, merged: { status: 'merged' }, noCommit: { commit_sha: '' } }).map(([id, change]) => [id, { ...task, id, task: id, worktree_boundary: { ...boundary, ...change } }]));
  await act(() => view.store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { released: task, ...excluded, reassigned: { ...task, id: 'reassigned', agent_id: 'peer' } } })));
  view.update({}, true, toAgentViewModel('qa', { ...agent.raw, worktree_repo_root: '/repo', worktree_base_branch: 'main' }));
  await screen.findByRole('region'); const selection = screen.getByRole('combobox', { name: 'Merge task' }); expect(within(selection).getAllByRole('option')).toHaveLength(2);
  fireEvent.change(selection, { target: { value: 'released' } }); fireEvent.click(screen.getByRole('button', { name: 'Create PR & merge' })); expect(requests.writes()[0]).toMatchObject({ merge_task_id: 'released' }); await screen.findByText('Refused');
});
